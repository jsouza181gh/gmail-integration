from sqlalchemy import func, select, update
from sqlalchemy.dialects import postgresql, sqlite

from src.db.session import make_session_factory
from src.db.models import Email, Run, RunEmail, WatchState


def _as_dict(obj):
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def _insert_ignore(session, model, rows):
    """INSERT ... ON CONFLICT DO NOTHING, portable across postgres/sqlite."""
    if not rows:
        return
    dialect = postgresql if session.get_bind().dialect.name == "postgresql" else sqlite
    session.execute(dialect.insert(model).values(rows).on_conflict_do_nothing())


class Store:
    """SQLAlchemy persistence: fetch cursor per run, classification per email
    and the Gmail watch state.

    Schema is managed by Alembic. Every write is its own transaction, so a
    crash at any point loses at most the batch in flight.
    """

    def __init__(self, session_factory=None):
        self.sf = session_factory or make_session_factory()

    def close(self):
        engine = self.sf.kw["bind"]
        engine.dispose()

    # --- runs -------------------------------------------------------------

    def get_or_create_run(self, query, force_new=False):
        """Resume the latest unfinished run for this query, else start one."""
        with self.sf.begin() as s:
            if not force_new:
                run = s.scalars(
                    select(Run).where(Run.query == query).order_by(Run.id.desc())
                ).first()
                if run and (not run.fetch_done or self._pending(s, run.id)):
                    return _as_dict(run)
            run = Run(query=query)
            s.add(run)
            s.flush()
            s.refresh(run)
            return _as_dict(run)

    def get_run(self, run_id):
        with self.sf() as s:
            return _as_dict(s.get(Run, run_id))

    def reset_cursor(self, run_id):
        with self.sf.begin() as s:
            s.execute(update(Run).where(Run.id == run_id).values(page_token=None))

    # --- fetch ------------------------------------------------------------

    def known_ids(self, ids):
        if not ids:
            return set()
        with self.sf() as s:
            return set(s.scalars(select(Email.id).where(Email.id.in_(ids))))

    @staticmethod
    def _ingest(s, run_id, all_ids, new_emails):
        _insert_ignore(
            s,
            Email,
            [
                {
                    "id": e["id"],
                    "sender": e["from"],
                    "subject": e["subject"],
                    "date": e["date"],
                    "snippet": e["snippet"],
                }
                for e in new_emails
            ],
        )
        _insert_ignore(
            s, RunEmail, [{"run_id": run_id, "email_id": i} for i in all_ids]
        )

    def ingest(self, run_id, all_ids, new_emails):
        """Persist fetched emails and link them to the run, without a cursor."""
        with self.sf.begin() as s:
            self._ingest(s, run_id, all_ids, new_emails)

    def save_page(self, run_id, all_ids, new_emails, next_token):
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
    def _pending(s, run_id):
        return s.scalar(
            select(func.count())
            .select_from(Email)
            .join(RunEmail, RunEmail.email_id == Email.id)
            .where(RunEmail.run_id == run_id, Email.category.is_(None))
        )

    def count_pending(self, run_id):
        with self.sf() as s:
            return self._pending(s, run_id)

    def pending(self, run_id, limit):
        with self.sf() as s:
            rows = s.scalars(
                select(Email)
                .join(RunEmail, RunEmail.email_id == Email.id)
                .where(RunEmail.run_id == run_id, Email.category.is_(None))
                .order_by(Email.attempts, RunEmail.added_at, Email.id)
                .limit(limit)
            )
            return [_as_dict(r) for r in rows]

    def save_classifications(self, results):
        """results: iterable of (email_id, category, reason)."""
        with self.sf.begin() as s:
            for eid, cat, reason in results:
                s.execute(
                    update(Email)
                    .where(Email.id == eid)
                    .values(category=cat, reason=reason, last_error=None)
                )

    def bump_attempts(self, ids, error):
        if not ids:
            return
        with self.sf.begin() as s:
            s.execute(
                update(Email)
                .where(Email.id.in_(list(ids)))
                .values(attempts=Email.attempts + 1, last_error=str(error)[:300])
            )

    def give_up(self, max_attempts, category, reason):
        """Close out emails that keep failing so they don't block the run."""
        with self.sf.begin() as s:
            s.execute(
                update(Email)
                .where(Email.category.is_(None), Email.attempts >= max_attempts)
                .values(category=category, reason=reason)
            )

    # --- report -----------------------------------------------------------

    def report_rows(self, run_id):
        with self.sf() as s:
            rows = s.scalars(
                select(Email)
                .join(RunEmail, RunEmail.email_id == Email.id)
                .where(RunEmail.run_id == run_id)
                .order_by(RunEmail.added_at, Email.id)
            )
            return [_as_dict(r) for r in rows]

    # --- gmail watch ------------------------------------------------------

    def get_watch_state(self):
        with self.sf() as s:
            state = s.get(WatchState, 1)
            return _as_dict(state) if state else None

    def init_watch_state(self, email_address, history_id, expiration):
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

    def set_history_id(self, history_id):
        with self.sf.begin() as s:
            s.execute(update(WatchState).where(WatchState.id == 1).values(history_id=history_id))
