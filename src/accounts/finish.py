"""Onboarding step 6 ("Pronto"): the last screen, after the keys."""
from sqlalchemy.orm import Session, sessionmaker

from src.auth.onboarding import ONBOARDING_STEPS, next_step
from src.db.models import User

FINISH_STEP = "finish"  # its place in the order is in ONBOARDING_STEPS
# steps from which the finish screen may be confirmed (None = already onboarded)
FINISHABLE = frozenset({None, FINISH_STEP, ONBOARDING_STEPS[-1]})


def can_finish(step: str | None) -> bool:
    return step in FINISHABLE


def complete(sf: sessionmaker[Session], user_id: int) -> None:
    """Onboarding is over: the user is on the last entry (`done`)."""
    with sf.begin() as s:
        user = s.get(User, user_id)
        if user is not None and user.onboarding_step == FINISH_STEP:
            user.onboarding_step = next_step(FINISH_STEP)
