from collections.abc import Iterable, Mapping, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session, sessionmaker

from src.classification.taxonomy import (
    CategoryDef,
    Classification,
    FlagDef,
    PreferenceSeed,
    Taxonomy,
    TaxonomySpec,
)
from src.db.models import (
    Base,
    Category,
    CategoryExample,
    CategoryFlag,
    CategorySet,
    Email,
    KbEntry,
    Run,
    RunEmail,
    WatchState,
)
from src.db.rows import EmailRow, RunRow, WatchStateRow
from src.db.session import make_session_factory
from src.gmail.messages import MessageSummary


def _insert_ignore(
    session: Session, model: type[Base], rows: Sequence[Mapping[str, object]]
) -> None:
    """INSERT ... ON CONFLICT DO NOTHING, portable across postgres/sqlite."""
    if not rows:
        return
    if session.get_bind().dialect.name == "postgresql":
        session.execute(postgresql.insert(model).values(rows).on_conflict_do_nothing())
    else:
        session.execute(sqlite.insert(model).values(rows).on_conflict_do_nothing())


class Store:
    """SQLAlchemy persistence: fetch cursor per run, classification per email,
    taxonomy, knowledge base and the Gmail watch state.

    Schema is managed by Alembic. Every write is its own transaction, so a
    crash at any point loses at most the batch in flight. Reads hand out
    typed rows (`src.db.rows`), never ORM objects.
    """

    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self.sf = session_factory or make_session_factory()

    def close(self) -> None:
        with self.sf() as s:
            s.get_bind().engine.dispose()

    # --- runs -------------------------------------------------------------

    def get_or_create_run(self, query: str, force_new: bool = False) -> RunRow:
        """Resume the latest unfinished run for this query, else start one."""
        with self.sf.begin() as s:
            if not force_new:
                run = s.scalars(
                    select(Run).where(Run.query == query).order_by(Run.id.desc())
                ).first()
                if run and (not run.fetch_done or self._pending(s, run.id)):
                    return RunRow.model_validate(run)
            run = Run(query=query)
            s.add(run)
            s.flush()
            s.refresh(run)
            return RunRow.model_validate(run)

    def get_run(self, run_id: int) -> RunRow:
        with self.sf() as s:
            return RunRow.model_validate(s.get_one(Run, run_id))

    def reset_cursor(self, run_id: int) -> None:
        with self.sf.begin() as s:
            s.execute(update(Run).where(Run.id == run_id).values(page_token=None))

    # --- fetch ------------------------------------------------------------

    def known_ids(self, ids: Sequence[str]) -> set[str]:
        if not ids:
            return set()
        with self.sf() as s:
            return set(s.scalars(select(Email.id).where(Email.id.in_(ids))))

    @staticmethod
    def _ingest(
        s: Session, run_id: int, all_ids: Sequence[str], new_emails: Sequence[MessageSummary]
    ) -> None:
        _insert_ignore(
            s,
            Email,
            [
                {
                    "id": e.id,
                    "sender": e.sender,
                    "subject": e.subject,
                    "date": e.date,
                    "snippet": e.snippet,
                }
                for e in new_emails
            ],
        )
        _insert_ignore(
            s, RunEmail, [{"run_id": run_id, "email_id": i} for i in all_ids]
        )

    def ingest(
        self, run_id: int, all_ids: Sequence[str], new_emails: Sequence[MessageSummary]
    ) -> None:
        """Persist fetched emails and link them to the run, without a cursor."""
        with self.sf.begin() as s:
            self._ingest(s, run_id, all_ids, new_emails)

    def save_page(
        self,
        run_id: int,
        all_ids: Sequence[str],
        new_emails: Sequence[MessageSummary],
        next_token: str | None,
    ) -> None:
        """Persist one fetched page and advance the cursor in one transaction."""
        with self.sf.begin() as s:
            self._ingest(s, run_id, all_ids, new_emails)
            s.execute(
                update(Run)
                .where(Run.id == run_id)
                .values(
                    page_token=next_token,
                    pages=Run.pages + 1,
                    fetch_done=next_token is None,
                )
            )

    # --- classification ---------------------------------------------------

    @staticmethod
    def _pending(s: Session, run_id: int) -> int:
        count = s.scalar(
            select(func.count())
            .select_from(Email)
            .join(RunEmail, RunEmail.email_id == Email.id)
            .where(RunEmail.run_id == run_id, Email.category.is_(None))
        )
        return count or 0

    def count_pending(self, run_id: int) -> int:
        with self.sf() as s:
            return self._pending(s, run_id)

    def pending(self, run_id: int, limit: int) -> list[EmailRow]:
        with self.sf() as s:
            rows = s.scalars(
                select(Email)
                .join(RunEmail, RunEmail.email_id == Email.id)
                .where(RunEmail.run_id == run_id, Email.category.is_(None))
                .order_by(Email.attempts, RunEmail.added_at, Email.id)
                .limit(limit)
            )
            return [EmailRow.model_validate(r) for r in rows]

    def save_classifications(self, results: Mapping[str, Classification]) -> None:
        with self.sf.begin() as s:
            for eid, c in results.items():
                s.execute(
                    update(Email)
                    .where(Email.id == eid)
                    .values(
                        category=c.category,
                        reason=c.reason,
                        is_relevant=c.is_relevant,
                        deadline=c.deadline,
                        has_document=c.has_document,
                        ephemeral=c.ephemeral,
                        category_set_id=c.category_set_id,
                        last_error=None,
                    )
                )

    def bump_attempts(self, ids: Iterable[str], error: object) -> None:
        id_list = list(ids)
        if not id_list:
            return
        with self.sf.begin() as s:
            s.execute(
                update(Email)
                .where(Email.id.in_(id_list))
                .values(attempts=Email.attempts + 1, last_error=str(error)[:300])
            )

    def give_up(self, max_attempts: int, category: str, reason: str) -> None:
        """Close out emails that keep failing so they don't block the run."""
        with self.sf.begin() as s:
            s.execute(
                update(Email)
                .where(Email.category.is_(None), Email.attempts >= max_attempts)
                .values(category=category, reason=reason)
            )

    # --- report -----------------------------------------------------------

    def report_rows(self, run_id: int) -> list[EmailRow]:
        with self.sf() as s:
            rows = s.scalars(
                select(Email)
                .join(RunEmail, RunEmail.email_id == Email.id)
                .where(RunEmail.run_id == run_id)
                .order_by(RunEmail.added_at, Email.id)
            )
            return [EmailRow.model_validate(r) for r in rows]

    # --- gmail watch ------------------------------------------------------

    def get_watch_state(self) -> WatchStateRow | None:
        with self.sf() as s:
            state = s.get(WatchState, 1)
            return WatchStateRow.model_validate(state) if state else None

    def init_watch_state(self, email_address: str, history_id: str, expiration: int) -> None:
        """Create the state, or on renewal update only address/expiration.

        The saved history_id is the sync cursor; a renewed watch returns a
        newer id that must NOT replace it or mail in between would be lost.
        """
        with self.sf.begin() as s:
            state = s.get(WatchState, 1)
            if state is None:
                s.add(
                    WatchState(
                        id=1,
                        email_address=email_address,
                        history_id=history_id,
                        expiration=expiration,
                    )
                )
            else:
                state.email_address = email_address
                state.expiration = expiration

    def set_history_id(self, history_id: str) -> None:
        with self.sf.begin() as s:
            s.execute(update(WatchState).where(WatchState.id == 1).values(history_id=history_id))

    # --- taxonomy ---------------------------------------------------------

    def create_category_set(self, spec: TaxonomySpec, activate: bool = True) -> int:
        """Store spec as a new version; activating it deactivates the others."""
        with self.sf.begin() as s:
            current = s.scalar(select(func.max(CategorySet.version))) or 0
            if activate:
                s.execute(update(CategorySet).values(active=False))
            cs = CategorySet(version=current + 1, active=activate)
            s.add(cs)
            s.flush()
            for f in spec.flags:
                s.add(CategoryFlag(set_id=cs.id, name=f.name, definition=f.definition))
            for pos, c in enumerate(spec.categories):
                cat = Category(
                    set_id=cs.id,
                    name=c.name,
                    definition=c.definition,
                    color=c.color,
                    position=pos,
                )
                s.add(cat)
                s.flush()
                s.add_all(
                    CategoryExample(category_id=cat.id, position=i, text=t)
                    for i, t in enumerate(c.examples)
                )
            return cs.id

    def active_taxonomy(self) -> Taxonomy | None:
        with self.sf() as s:
            cs = s.scalars(select(CategorySet).where(CategorySet.active)).first()
            if cs is None:
                return None
            cats = s.scalars(
                select(Category).where(Category.set_id == cs.id).order_by(Category.position)
            ).all()
            examples: dict[int, list[str]] = {c.id: [] for c in cats}
            for ex in s.scalars(
                select(CategoryExample)
                .where(CategoryExample.category_id.in_(list(examples)))
                .order_by(CategoryExample.category_id, CategoryExample.position)
            ):
                examples[ex.category_id].append(ex.text)
            flags = s.scalars(
                select(CategoryFlag).where(CategoryFlag.set_id == cs.id).order_by(CategoryFlag.id)
            ).all()
            spec = TaxonomySpec(
                categories=tuple(
                    CategoryDef(
                        name=c.name,
                        definition=c.definition,
                        examples=tuple(examples[c.id]),
                        color=c.color,
                    )
                    for c in cats
                ),
                flags=tuple(
                    FlagDef.model_validate({"name": f.name, "definition": f.definition})
                    for f in flags
                ),
            )
            return Taxonomy(set_id=cs.id, version=cs.version, spec=spec)

    # --- knowledge base ---------------------------------------------------

    def add_preference(self, seed: PreferenceSeed, source: str) -> int:
        with self.sf.begin() as s:
            entry = KbEntry(
                kind="preference", scope=seed.scope, text=seed.text, source=source, active=True
            )
            s.add(entry)
            s.flush()
            return entry.id

    def active_preferences(self, scope: str) -> list[str]:
        with self.sf() as s:
            return list(
                s.scalars(
                    select(KbEntry.text)
                    .where(KbEntry.active, KbEntry.kind == "preference", KbEntry.scope == scope)
                    .order_by(KbEntry.id)
                )
            )
