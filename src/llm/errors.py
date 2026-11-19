class LLMError(Exception):
    """Base of every gateway error."""


class LLMFatalError(LLMError):
    """Not recoverable by retrying (bad key, bad request)."""


class LLMExhaustedError(LLMError):
    """Every attempt failed with a recoverable error."""
