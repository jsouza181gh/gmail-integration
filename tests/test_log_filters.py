import logging

from src.api.app import create_app
from src.api.log_filters import StripQueryString


def access_record(path):
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "POST", path, "1.0", 204), None,
    )


def test_strips_query_string():
    rec = access_record("/webhooks/gmail?token=secret")
    assert StripQueryString().filter(rec) is True
    assert "secret" not in rec.getMessage()
    assert '"POST /webhooks/gmail HTTP/1.0"' in rec.getMessage()


def test_keeps_path_without_query():
    rec = access_record("/healthz")
    StripQueryString().filter(rec)
    assert '"POST /healthz HTTP/1.0"' in rec.getMessage()


def test_ignores_unexpected_record_shape():
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "texto?token=x", None, None)
    assert StripQueryString().filter(rec) is True
    assert rec.getMessage() == "texto?token=x"


def test_create_app_installs_filter_once():
    create_app(trigger=lambda: None, token=None)
    create_app(trigger=lambda: None, token=None)
    filters = logging.getLogger("uvicorn.access").filters
    assert sum(isinstance(f, StripQueryString) for f in filters) == 1
