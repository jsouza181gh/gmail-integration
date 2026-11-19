"""/api/actions: the approval queue (decisions only; execution is the worker's)."""
from fastapi import APIRouter, Depends, Query, Request

from src.api import queries
from src.api.deps import get_deps, require_user
from src.llm.types import Schema

router = APIRouter(prefix="/api/actions", dependencies=[Depends(require_user)], tags=["actions"])
COMMENT_MAX = 500


class DecisionIn(Schema):
    ids: list[int]
    comment: str | None = None


class DecisionOut(Schema):
    decided: int


@router.get("")
def list_actions(
    request: Request,
    status: str = "pending",
    kind: str | None = None,
    sender: str | None = None,
    rule_id: int | None = None,
    limit: int = Query(500, ge=1, le=1000),
) -> list[queries.ActionOut]:
    with get_deps(request).sf() as s:
        return queries.actions(s, status=status, kind=kind, sender=sender, rule_id=rule_id, limit=limit)


def _comment(body: DecisionIn) -> str | None:
    return (body.comment or "").strip()[:COMMENT_MAX] or None


@router.post("/approve")
def approve(body: DecisionIn, request: Request) -> DecisionOut:
    n = get_deps(request).approvals.approve(body.ids[:500], _comment(body), source="ui")
    return DecisionOut(decided=n)


@router.post("/reject")
def reject(body: DecisionIn, request: Request) -> DecisionOut:
    n = get_deps(request).approvals.reject(body.ids[:500], _comment(body), source="ui")
    return DecisionOut(decided=n)
