"""/api/emails and /api/categories: inbox views, detail, reclassification."""
from datetime import datetime
from email.utils import parseaddr

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from src.db.scope import email_key
from src.api import queries
from src.api.deps import get_deps, require_user
from src.db.models import Email, Feedback, KbEntry
from src.db.store import Store
from src.llm.types import Schema

router = APIRouter(prefix="/api", dependencies=[Depends(require_user)], tags=["mail"])
COMMENT_MAX = 500


class ReclassifyIn(Schema):
    category: str
    comment: str | None = None


class CategoryOut(Schema):
    name: str
    definition: str
    color: str | None


class FlagOut(Schema):
    name: str
    definition: str


class TaxonomyOut(Schema):
    version: int
    categories: list[CategoryOut]
    flags: list[FlagOut]


def _today(request: Request) -> datetime:
    return get_deps(request).clock()


@router.get("/emails")
def list_emails(
    request: Request,
    category: str | None = None,
    relevant: bool | None = None,
    q: str | None = None,
    before: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> queries.EmailPage:
    with get_deps(request).sf() as s:
        return queries.list_emails(
            s, category=category, relevant=relevant, q=q, before=before, limit=limit,
            today=_today(request).date(),
        )


@router.get("/emails/relevant")
def relevant(request: Request) -> list[queries.EmailListItem]:
    with get_deps(request).sf() as s:
        return queries.relevant(s, _today(request).date())


@router.get("/emails/{email_id}")
def detail(email_id: str, request: Request) -> queries.EmailDetail:
    with get_deps(request).sf() as s:
        d = queries.email_detail(s, email_id, _today(request).date())
    if d is None:
        raise HTTPException(404, "e-mail não encontrado")
    return d


@router.post("/emails/{email_id}/reclassify", status_code=204)
def reclassify(email_id: str, body: ReclassifyIn, request: Request) -> None:
    deps = get_deps(request)
    taxonomy = Store(deps.sf).active_taxonomy()
    if taxonomy is None or body.category not in taxonomy.names:
        raise HTTPException(422, "categoria fora da taxonomia ativa")
    comment = (body.comment or "").strip()[:COMMENT_MAX] or None
    now = deps.clock()
    with deps.sf.begin() as s:
        email = s.get(Email, email_key(email_id))
        if email is None:
            raise HTTPException(404, "e-mail não encontrado")
        email.category = body.category
        email.category_set_id = taxonomy.set_id
        email.reason = "reclassificado pelo usuário"
        # Only the address and the user's words reach the prompt (no subject).
        who = parseaddr(email.sender or "")[1] or "remetente sem endereço"
        text = f"E-mails de {who} são {body.category}."
        if comment:
            text += f" Comentário do usuário: {comment}"
        kb = KbEntry(kind="preference", scope="classification", text=text, source="ui",
                     active=True, created_at=now)
        s.add(kb)
        s.flush()
        s.add(Feedback(email_id=email_id, verdict="reclassify", text=comment, source="ui",
                       kb_entry_id=kb.id, created_at=now))


@router.get("/categories")
def categories(request: Request) -> TaxonomyOut:
    taxonomy = Store(get_deps(request).sf).active_taxonomy()
    if taxonomy is None:
        raise HTTPException(503, "nenhuma taxonomia ativa")
    return TaxonomyOut(
        version=taxonomy.version,
        categories=[CategoryOut(name=c.name, definition=c.definition, color=c.color)
                    for c in taxonomy.spec.categories],
        flags=[FlagOut(name=f.name, definition=f.definition) for f in taxonomy.spec.flags],
    )

