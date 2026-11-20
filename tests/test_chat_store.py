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
