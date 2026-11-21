import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.agent.types import ActionsDraft, DocumentsDraft, EmailsDraft, RuleDraft
from src.chat.audio import parse_wav
from src.chat.store import ChatConflict, ChatStore, TurnBusy
from src.db.models import Attachment, ChatCard
from tests.chat_fakes import add_email, wav


@pytest.fixture
def store(session_factory: sessionmaker[Session]) -> ChatStore:
    return ChatStore(session_factory)


def test_start_turn_text_and_busy_conversation(store: ChatStore) -> None:
    conv = store.create_conversation()
    turn = store.start_turn(conv.id, text="  Quanto gastei com a Light?  ")
    assert (turn.user.role, turn.user.status, turn.user.text) == (
        "user", "done", "Quanto gastei com a Light?"
    )
    assert (turn.assistant.role, turn.assistant.status) == ("assistant", "queued")
    with pytest.raises(TurnBusy):
        store.start_turn(conv.id, text="e agora?")
    assert store.begin(turn.assistant.id) is True
    assert store.begin(turn.assistant.id) is False
    store.finish(turn.assistant.id, "R$ 10")
    assert store.start_turn(conv.id, text="valeu").assistant.status == "queued"
    (row,) = store.conversations(10)
    assert row.title == "Quanto gastei com a Light?" and row.busy is True


def test_audio_turn_stores_original(store: ChatStore) -> None:
    conv = store.create_conversation()
    data = wav(2.5)
    turn = store.start_turn(conv.id, audio=(data, parse_wav(data)))
    assert (turn.user.input, turn.user.status, turn.user.audio_ms) == ("audio", "queued", 2500)
    assert store.audio(turn.user.id) == ("audio/wav", data)
    store.set_transcript(turn.user.id, "tudo da loja pode ir pra lixeira")
    user = store.turn(turn.assistant.id).user
    assert (user.text, user.status) == ("tudo da loja pode ir pra lixeira", "done")
    assert store.conversations(1)[0].title == "tudo da loja pode ir pra lixeira"


def test_start_turn_needs_text_or_audio(store: ChatStore) -> None:
    conv = store.create_conversation()
    with pytest.raises(ValueError):
        store.start_turn(conv.id, text="   ")
    with pytest.raises(LookupError):
        store.start_turn(999, text="oi")


def test_retry_only_failed(store: ChatStore) -> None:
    conv = store.create_conversation()
    turn = store.start_turn(conv.id, text="oi")
    with pytest.raises(ChatConflict):
        store.retry(turn.assistant.id)
    store.begin(turn.assistant.id)
    store.fail(turn.assistant.id, "O Gemini está sobrecarregado agora.")
    again = store.retry(turn.assistant.id)
    assert again.assistant.id != turn.assistant.id
    assert again.user.id == turn.user.id and again.assistant.status == "queued"
    old = store.turn(turn.assistant.id).assistant
    assert (old.status, old.error) == ("failed", "O Gemini está sobrecarregado agora.")


def test_history_includes_card_outcomes(
    store: ChatStore, session_factory: sessionmaker[Session]
) -> None:
    conv = store.create_conversation()
    first = store.start_turn(conv.id, text="regra pra loja")
    store.begin(first.assistant.id)
    card_id = store.add_card(first.assistant.id, "chat", RuleDraft(
        name="lixo da loja", action="trash", sender_domain="loja.com", affected=3,
    ))
    store.finish(first.assistant.id, "Entendi assim — confere?")
    with session_factory.begin() as s:
        s.get_one(ChatCard, card_id).status = "confirmed"
    second = store.start_turn(conv.id, text="e mais?")
    lines = store.history(conv.id, before_id=second.user.id)
    assert [line.role for line in lines] == ["user", "assistant"]
    assert lines[1].text.startswith("Entendi assim")
    assert "regra 'lixo da loja'" in lines[1].text and "confirmado" in lines[1].text


def test_turn_view_resolves_item_titles(
    store: ChatStore, session_factory: sessionmaker[Session]
) -> None:
    add_email(session_factory, "e1", subject="Boleto Light", sender="Light <conta@light.com>")
    with session_factory.begin() as s:
        s.add(Attachment(email_id="e1", part_id="2", filename="boleto.pdf",
                         mime_type="application/pdf", size=41_000, status="done",
                         title="Boleto Light novembro", doc_type="boleto"))
    conv = store.create_conversation()
    turn = store.start_turn(conv.id, text="boleto")
    store.add_card(turn.assistant.id, "chat", EmailsDraft(email_ids=("e1",)))
    store.add_card(turn.assistant.id, "chat", DocumentsDraft(attachment_ids=(1,)))
    store.add_card(turn.assistant.id, "audio", ActionsDraft(action_ids=(), verdict="reject"))
    emails, docs, actions = store.turn(turn.assistant.id).assistant.cards
    assert (emails.kind, emails.status) == ("emails", "info")
    assert emails.items[0].title == "Boleto Light" and "light.com" in emails.items[0].subtitle
    assert docs.items[0].title == "Boleto Light novembro"
    assert docs.items[0].attachment_id == 1 and "boleto" in docs.items[0].subtitle
    assert (actions.status, actions.channel, actions.verdict) == ("proposed", "audio", "reject")


def _answered(store: ChatStore, text: str = "oi") -> tuple[int, int]:
    conv = store.create_conversation()
    turn = store.start_turn(conv.id, text=text)
    store.begin(turn.assistant.id)
    return conv.id, turn.assistant.id


def test_add_refs_dedupes_and_caps(store: ChatStore, session_factory: sessionmaker[Session]) -> None:
    from src.agent.types import EmailRef
    from src.db.models import ChatMessageRef

    for i in range(23):
        add_email(session_factory, f"m{i}", subject=f"assunto {i}")
    _, answer = _answered(store)
    refs = [EmailRef(email_id=f"m{i}", subject=f"assunto {i}", sender="a@b.c") for i in range(23)]
    store.add_refs(answer, refs[:3])
    store.add_refs(answer, [refs[0], refs[1], *refs[3:]])
    with session_factory() as s:
        got = s.query(ChatMessageRef).order_by(ChatMessageRef.position).all()
    assert [r.email_id for r in got] == [f"m{i}" for i in range(20)]


def test_history_renders_refs(store: ChatStore, session_factory: sessionmaker[Session]) -> None:
    from src.agent.types import EmailRef

    for i in range(12):
        add_email(session_factory, f"m{i}")
    conv, answer = _answered(store)
    store.add_refs(answer, [EmailRef(email_id=f"m{i}", subject=f"Alerta {i}",
                                     sender="Google Alerts <g@google.com>") for i in range(12)])
    store.finish(answer, "Achei estes.")
    nxt = store.start_turn(conv, text="apaga esses")
    (_, bot) = store.history(conv, before_id=nxt.user.id)
    line = bot.text.splitlines()[-1]
    assert line.startswith("[e-mails desta resposta: m0 — Alerta 0 (Google Alerts <g@google.com>); ")
    assert "m9 — Alerta 9" in line and "m10" not in line


def _approved_card(store: ChatStore, sf: sessionmaker[Session], n: int = 3,
                   batch: bool = True) -> tuple[int, int, list[int]]:
    from datetime import UTC, datetime

    from src.db.models import SuggestedAction

    ids: list[int] = []
    with sf.begin() as s:
        for i in range(n):
            s.add(SuggestedAction(email_id=f"m{i}", kind="trash", reason="spam", status="approved",
                                  origin="agent", created_at=datetime.now(UTC)))
        s.flush()
        ids = [a.id for a in s.query(SuggestedAction).order_by(SuggestedAction.id)]
    conv = store.create_conversation()
    turn = store.start_turn(conv.id, text="apaga o spam")
    store.begin(turn.assistant.id)
    draft = ActionsDraft(action_ids=tuple(ids), action="trash", verdict="approve")
    if batch:
        draft = draft.model_copy(update={"batch_query": "in:spam", "batch_total": 120, "batch_no": 1})
    card = store.add_card(turn.assistant.id, "chat", draft)
    store.finish(turn.assistant.id, "Lote 1 de 3.")
    return conv.id, card, ids


def _set(sf: sessionmaker[Session], action_id: int, status: str, error: str | None = None) -> None:
    from src.db.models import SuggestedAction

    with sf.begin() as s:
        a = s.get_one(SuggestedAction, action_id)
        a.status, a.error = status, error


def test_continuation_waits_for_the_card_and_reports_results(
    store: ChatStore, session_factory: sessionmaker[Session]
) -> None:
    for i in range(3):
        add_email(session_factory, f"m{i}")
    conv, card, ids = _approved_card(store, session_factory)
    turn = store.start_event_turn(conv, "[evento] aprovou", wait_card_id=card)
    assert turn is not None
    assert store.settle_card(card) is None
    assert store.turn(turn.assistant.id).assistant.progress == "apagando… 0/3"
    _set(session_factory, ids[0], "executed")
    assert store.settle_card(card) is None
    assert store.turn(turn.assistant.id).assistant.progress == "apagando… 1/3"
    _set(session_factory, ids[1], "executed")
    _set(session_factory, ids[2], "failed", "404 not found")
    assert store.settle_card(card) == turn.assistant.id
    done = store.turn(turn.assistant.id)
    assert done.assistant.progress is None and done.assistant.status == "queued"
    assert done.user.text == (
        f"[evento] Lote 1 de 3 concluído (cartão #{card}): 2 e-mail(s) apagado(s), 1 falhou"
        " (404 not found). Restam ~70 na busca 'in:spam'."
    )
    assert store.settle_card(card) is None  # already released


def test_settle_releases_at_once_when_everything_already_ran(
    store: ChatStore, session_factory: sessionmaker[Session]
) -> None:
    for i in range(2):
        add_email(session_factory, f"m{i}")
    conv, card, ids = _approved_card(store, session_factory, n=2, batch=False)
    for a in ids:
        _set(session_factory, a, "executed")
    turn = store.start_event_turn(conv, "[evento] aprovou", wait_card_id=card)
    assert turn is not None and store.settle_card(card) == turn.assistant.id
    assert store.turn(turn.assistant.id).user.text == (
        f"[evento] Cartão #{card} concluído: 2 e-mail(s) apagado(s)."
    )
