from src.llm.types import ErrorKind


class LLMError(Exception):
    """Base of every gateway error."""


class LLMFatalError(LLMError):
    """Not recoverable by retrying (bad key, bad request)."""


class LLMExhaustedError(LLMError):
    """Every attempt failed with a recoverable error (or the quota is gone)."""

    def __init__(self, message: str, kinds: tuple[ErrorKind, ...] = ()) -> None:
        super().__init__(message)
        self.kinds = kinds


def quota_exhausted(exc: LLMExhaustedError) -> bool:
    """Only rate limits / overload: retrying later (not now) is the fix."""
    return bool(exc.kinds) and all(k in ("rate", "transient") for k in exc.kinds)
