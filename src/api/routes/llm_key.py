"""/api/onboarding/llm-key: the user's own Gemini key (onboarding step 5).

The key is checked with a cheap real call before it is stored; the panel never
gets a key back, only whether one is set and working.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field

from src.api.deps import get_deps, request_ip, require_user
from src.auth.onboarding import ONBOARDING_STEPS, is_complete
from src.db.scope import current_account
from src.llm.keys import key_status, save_keys
from src.llm.types import Schema

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])
STEP = "llm_key"  # its place in the order is in ONBOARDING_STEPS
KEY_MAX = 200


class LlmKeyIn(Schema):
    app_key: str = Field(min_length=1, max_length=KEY_MAX)
    backfill_key: str | None = Field(default=None, max_length=KEY_MAX)


class LlmKeyOut(Schema):
    configured: bool
    has_backfill_key: bool
    validated_at: datetime | None
    last_error: str | None
    next_step: str | None


def _next_step() -> str | None:
    steps = ONBOARDING_STEPS
    i = steps.index(STEP)
    return steps[i + 1] if i + 1 < len(steps) else None


def _clean(raw: str | None) -> str | None:
    value = (raw or "").strip().strip("\"'")
    return value or None


@router.get("/llm-key")
def get_llm_key(request: Request, user_id: int = Depends(require_user)) -> LlmKeyOut:
    status = key_status(get_deps(request).sf, user_id)
    return LlmKeyOut(**status.model_dump(), next_step=_next_step())


@router.post("/llm-key")
def post_llm_key(
    body: LlmKeyIn, request: Request, user_id: int = Depends(require_user)
) -> LlmKeyOut:
    deps = get_deps(request)
    app_key = _clean(body.app_key)
    if app_key is None:
        raise HTTPException(422, "Cole a chave do Gemini no campo.")
    backfill_key = _clean(body.backfill_key)
    if backfill_key == app_key:
        backfill_key = None
    problem = deps.key_validator(app_key)
    if problem is not None:
        raise HTTPException(422, problem)
    if backfill_key is not None:
        problem = deps.key_validator(backfill_key)
        if problem is not None:
            raise HTTPException(422, f"A segunda chave (avançado): {problem}")
    save_keys(deps.sf, user_id, app_key=app_key, backfill_key=backfill_key, now=deps.clock())
    deps.llm_keys.invalidate(current_account())
    out = get_llm_key(request, user_id)
    was_done = is_complete(deps.onboarding.state(user_id).step)
    step = deps.onboarding.finish_step(user_id, STEP)
    if not was_done and is_complete(step):
        deps.log.record("onboarding_done", request_ip(request), f"usuário {user_id}")
    return out.model_copy(update={"next_step": step})
