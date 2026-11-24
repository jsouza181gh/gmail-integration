import inspect

import pytest

import src.gmail.actions as actions_module
from src.agent.types import ACTION_KINDS, ActionKind
from src.gmail.actions import GmailActions, MissingScope
from src.gmail.client import SETTINGS_SCOPE
from tests.agent_fakes import make_email
from tests.fakes import FakeService


def ok(**kw: object) -> dict[str, str]:
    return {}


def _svc() -> FakeService:
    return FakeService(
        messages__modify=ok,
        messages__trash=ok,
        settings__filters__create=ok,
    )


def _gmail(svc: FakeService, *, scopes: tuple[str, ...] = (SETTINGS_SCOPE,)) -> GmailActions:
    return GmailActions(lambda: svc, has_scope=lambda scope: scope in scopes)


@pytest.mark.parametrize(
    ("kind", "call", "body"),
    [
        ("mark_read", "messages.modify", {"removeLabelIds": ["UNREAD"]}),
        ("star", "messages.modify", {"addLabelIds": ["STARRED"]}),
        ("archive", "messages.modify", {"removeLabelIds": ["INBOX"]}),
        ("trash", "messages.trash", None),
        (
            "block_sender",
            "settings.filters.create",
            {
                "criteria": {"from": "ofertas@loja.com"},
                "action": {"addLabelIds": ["TRASH"], "removeLabelIds": ["INBOX"]},
            },
        ),
    ],
)
def test_each_kind_maps_to_gmail_call(
    kind: ActionKind, call: str, body: dict[str, object] | None
) -> None:
    svc = _svc()
    _gmail(svc).execute(kind, make_email("m1"))
    ((name, kw),) = svc.calls
    assert name == call
    if body is not None:
        assert kw["body"] == body
    if name.startswith("messages."):
        assert kw["id"] == "m1"


def test_block_sender_without_scope_raises_missing_scope() -> None:
    svc = _svc()
    with pytest.raises(MissingScope, match="reconecte o Gmail"):
        _gmail(svc, scopes=()).execute("block_sender", make_email("m1"))
    assert svc.calls == []


def test_block_sender_without_address_fails() -> None:
    with pytest.raises(ValueError):
        _gmail(_svc()).execute("block_sender", make_email("m1", sender="sem endereço"))


def test_no_permanent_delete_anywhere() -> None:
    source = inspect.getsource(actions_module)
    assert ".delete(" not in source and "batchDelete" not in source
    svc = _svc()
    for kind in ACTION_KINDS:
        _gmail(svc).execute(kind, make_email("m1"))
    assert not any("delete" in name.lower() for name, _ in svc.calls)
