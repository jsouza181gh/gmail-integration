from datetime import datetime, timezone

import pytest

from src.gmail.messages import message_summary
from tests.fakes import FakeService


def svc_with(date: str | None = "Mon, 16 Nov 2026 10:00:00 +0000", snippet: str = "oi") -> FakeService:
    headers = [{"name": "From", "value": "a@x"}, {"name": "Subject", "value": "s"}]
    if date is not None:
        headers.append({"name": "Date", "value": date})
    return FakeService(
        messages__get=lambda **kw: {"id": kw["id"], "snippet": snippet, "payload": {"headers": headers}}
    )


def test_summary_parses_date_header_to_utc() -> None:
    s = message_summary(svc_with(date="Tue, 17 Nov 2026 02:25:56 -0300"), "a")
    assert s.received_at == datetime(2026, 11, 17, 5, 25, 56, tzinfo=timezone.utc)


@pytest.mark.parametrize("date", ["", "ontem", None])
def test_summary_unparseable_date_is_none(date: str | None) -> None:
    assert message_summary(svc_with(date=date), "a").received_at is None


def test_snippet_keeps_200_chars() -> None:
    assert len(message_summary(svc_with(snippet="x" * 300), "a").snippet) == 200
