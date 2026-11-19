from sqlalchemy.orm import Session, sessionmaker

from src.gmail.labels import GmailLabeler
from tests.fakes import FakeService, http_error


def _svc(existing: list[dict[str, str]] | None = None, fail_modify: int = 0) -> FakeService:
    created: list[str] = []
    failures = [fail_modify]

    def create(**kw: object) -> dict[str, str]:
        body = kw["body"]
        assert isinstance(body, dict)
        created.append(str(body["name"]))
        return {"id": f"L{len(created)}", "name": str(body["name"])}

    def modify(**kw: object) -> dict[str, str]:
        if failures[0]:
            failures[0] -= 1
            raise http_error(400)
        return {}

    return FakeService(
        labels__list=lambda **kw: {"labels": existing or []},
        labels__create=create,
        messages__modify=modify,
    )


def _names(svc: FakeService, call: str) -> list[dict[str, object]]:
    return [kw for name, kw in svc.calls if name == call]


def test_labeler_creates_missing_label_once(session_factory: sessionmaker[Session]) -> None:
    svc = _svc()
    labeler = GmailLabeler(lambda: svc, session_factory)
    assert labeler.apply("m1", ["IA/Promoções"]) == ("IA/Promoções",)
    labeler.apply("m2", ["IA/Promoções"])
    assert len(_names(svc, "labels.create")) == 1
    mods = _names(svc, "messages.modify")
    assert [m["id"] for m in mods] == ["m1", "m2"]
    assert mods[0]["body"] == {"addLabelIds": ["L1"]}


def test_labeler_reuses_existing_gmail_label(session_factory: sessionmaker[Session]) -> None:
    svc = _svc(existing=[{"id": "X9", "name": "IA/Relevante", "type": "user"}])
    GmailLabeler(lambda: svc, session_factory).apply("m1", ["IA/Relevante"])
    assert _names(svc, "labels.create") == []
    assert _names(svc, "messages.modify")[0]["body"] == {"addLabelIds": ["X9"]}


def test_labeler_refreshes_stale_cache_once(session_factory: sessionmaker[Session]) -> None:
    svc = _svc(fail_modify=1)
    labeler = GmailLabeler(lambda: svc, session_factory)
    labeler.apply("m1", ["IA/Social"])
    assert len(_names(svc, "messages.modify")) == 2


def test_labeler_dry_run_touches_nothing(session_factory: sessionmaker[Session]) -> None:
    svc = _svc()
    assert GmailLabeler(lambda: svc, session_factory, dry_run=True).apply("m1", ["IA/X"]) == (
        "IA/X",
    )
    assert svc.calls == []
