from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import LlmCall, LlmCallEmail, LlmCallMessage
from src.llm.recorder import DbCallRecorder
from src.llm.types import CallContext, CallRecord, ChatMessage, TokenUsage, ToolCallRequest


def _record(**overrides: object) -> CallRecord:
    base = CallRecord(
        purpose="classify",
        model="m1",
        attempt=1,
        status="ok",
        latency_ms=120,
        usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15, cached_input_tokens=2),
        error_kind=None,
        error=None,
        request=(ChatMessage(role="system", content="s"), ChatMessage(role="human", content="oi")),
        response_text='{"x":1}',
        context=CallContext(run_id=None, email_ids=("a", "b")),
    )
    return base.model_copy(update=overrides)


def test_records_call_with_messages_and_emails(session_factory: sessionmaker[Session]) -> None:
    DbCallRecorder(session_factory).record(_record())
    with session_factory() as s:
        row = s.scalars(select(LlmCall)).one()
        msgs = s.scalars(select(LlmCallMessage).order_by(LlmCallMessage.position)).all()
        emails = set(s.scalars(select(LlmCallEmail.email_id)))
    assert (row.purpose, row.model, row.status, row.latency_ms) == ("classify", "m1", "ok", 120)
    assert (row.input_tokens, row.total_tokens, row.cached_input_tokens) == (10, 15, 2)
    assert row.response_text == '{"x":1}' and row.created_at is not None
    assert [(m.role, m.content) for m in msgs] == [("system", "s"), ("human", "oi")]
    assert emails == {"a", "b"}


def test_records_error_attempt(session_factory: sessionmaker[Session]) -> None:
    DbCallRecorder(session_factory).record(
        _record(status="error", error_kind="fatal", error="401", response_text=None)
    )
    with session_factory() as s:
        row = s.scalars(select(LlmCall)).one()
    assert (row.status, row.error_kind, row.error, row.response_text) == ("error", "fatal", "401", None)


def test_records_agent_run_and_step(session_factory: sessionmaker[Session]) -> None:
    DbCallRecorder(session_factory).record(
        _record(context=CallContext(agent_run_id=1, agent_step_id=2))
    )
    with session_factory() as s:
        row = s.scalars(select(LlmCall)).one()
    assert (row.agent_run_id, row.agent_step_id) == (1, 2)


def test_records_tool_calls_and_tool_results(session_factory: sessionmaker[Session]) -> None:
    call = ToolCallRequest(id="c1", name="get_email_body", args={"email_id": "a"})
    DbCallRecorder(session_factory).record(
        _record(
            request=(
                ChatMessage(role="ai", content="", tool_calls=(call,)),
                ChatMessage(role="tool", content="corpo", tool_call_id="c1"),
            )
        )
    )
    with session_factory() as s:
        msgs = s.scalars(select(LlmCallMessage).order_by(LlmCallMessage.position)).all()
    assert [(m.role, m.content) for m in msgs] == [
        ("ai", 'get_email_body({"email_id":"a"})'),
        ("tool", "corpo"),
    ]


def test_recorder_renders_parts_without_bytes(session_factory: sessionmaker[Session]) -> None:
    from src.llm.types import BinaryPart

    msg = ChatMessage(role="human", content="doc", parts=(BinaryPart(mime_type="application/pdf", data=b"SECRET-BYTES"),))
    DbCallRecorder(session_factory).record(_record(request=(msg,)))
    with session_factory() as s:
        content = s.scalars(select(LlmCallMessage.content)).one()
    assert content == "doc\n[anexo application/pdf, 12 bytes]"
