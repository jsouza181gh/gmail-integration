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
    return bool(exc.kinds) and all(k in ("rate", "transient", "quota") for k in exc.kinds)


class LLMWaitError(LLMExhaustedError):
    """The key must rest `seconds` (pacing or rate-limit backoff) and the gateway
    was told not to sleep that long inside a job: the job goes back to the queue
    with a delay, so the wait never holds a worker lane (or another user's turn)."""

    def __init__(self, seconds: float, message: str) -> None:
        super().__init__(message, kinds=("rate",))
        self.seconds = max(1, int(seconds))


NO_KEY_TEXT = (
    "Falta a chave do Gemini desta conta (ou ela foi recusada). "
    "Abra o painel e cadastre uma chave para o assistente voltar a trabalhar."
)


class NoLLMKeyError(LLMError):
    """The account has no usable Gemini key. Never answered with another
    account's key nor with a platform key."""

    def __init__(self, message: str = NO_KEY_TEXT) -> None:
        super().__init__(message)
