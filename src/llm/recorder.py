"""Persists gateway call records (satisfies `CallRecorder`)."""
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import LlmCall, LlmCallEmail, LlmCallMessage
from src.llm.types import CallRecord, render_turn


class DbCallRecorder:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.sf = session_factory

    def record(self, rec: CallRecord) -> None:
        with self.sf.begin() as s:
            call = LlmCall(
                purpose=rec.purpose,
                model=rec.model,
                attempt=rec.attempt,
                status=rec.status,
                error_kind=rec.error_kind,
                error=rec.error,
                latency_ms=rec.latency_ms,
                input_tokens=rec.usage.input_tokens,
                output_tokens=rec.usage.output_tokens,
                total_tokens=rec.usage.total_tokens,
                cached_input_tokens=rec.usage.cached_input_tokens,
                reasoning_tokens=rec.usage.reasoning_tokens,
                audio_input_tokens=rec.usage.audio_input_tokens,
                response_text=rec.response_text,
                run_id=rec.context.run_id,
                agent_run_id=rec.context.agent_run_id,
                agent_step_id=rec.context.agent_step_id,
            )
            s.add(call)
            s.flush()
            s.add_all(
                LlmCallMessage(
                    call_id=call.id,
                    position=i,
                    role=m.role,
                    content=render_turn(m.content, m.tool_calls, m.parts),
                )
                for i, m in enumerate(rec.request)
            )
            s.add_all(
                LlmCallEmail(call_id=call.id, email_id=e)
                for e in dict.fromkeys(rec.context.email_ids)
            )
