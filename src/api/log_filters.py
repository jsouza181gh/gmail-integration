"""Keep secrets passed in the query string (e.g. ?token=) out of access logs."""
import logging


class StripQueryString(logging.Filter):
    """Drop the query string from uvicorn access-log records.

    uvicorn logs args as (client, method, path_with_query, http_version, status).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            record.args = (*args[:2], args[2].split("?", 1)[0], *args[3:])
        return True


def install_access_log_filter() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, StripQueryString) for f in logger.filters):
        logger.addFilter(StripQueryString())
