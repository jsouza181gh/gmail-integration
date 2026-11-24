"""Onboarding step 6 ("Pronto"): the last screen, after the keys."""
from sqlalchemy.orm import Session, sessionmaker

from src.auth.onboarding import ONBOARDING_STEPS
from src.db.models import User

FINISH_STEP = "finish"
# steps from which the finish screen may be confirmed (None = already onboarded)
FINISHABLE = frozenset({None, "done_basic", FINISH_STEP})

if FINISH_STEP not in ONBOARDING_STEPS:  # phase 5 owns step 6, phase 3 owns the list
    ONBOARDING_STEPS.append(FINISH_STEP)


def can_finish(step: str | None) -> bool:
    return step in FINISHABLE


def complete(sf: sessionmaker[Session], user_id: int) -> None:
    """Onboarding is over: no step left (None)."""
    with sf.begin() as s:
        user = s.get(User, user_id)
        if user is not None:
            user.onboarding_step = None
