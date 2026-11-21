"""Agent worker process: `python -m src.worker`.

Consumes the Postgres job queue until SIGTERM/SIGINT. One LLM gateway per
process (free-mode pacing lives in it).
"""
import os
import signal
import socket
import threading
from datetime import timedelta
from pathlib import Path
from types import FrameType

from dotenv import load_dotenv

from src.db.store import Store
from src.jobs.store import JobStore
from src.jobs.types import ChatTurnJob, ClaimedJob, ExecuteActionJob, JobKind
from src.jobs.worker import Handler, Worker
from src.utils.console import log

HEARTBEAT = Path(os.getenv("WORKER_HEARTBEAT", "/tmp/worker-heartbeat"))
AGENT_FAILURE_STREAK = 3
CHAT_KINDS: frozenset[JobKind] = frozenset({"chat_turn"})


def main() -> None:
    load_dotenv()
    store = Store()
    jobs = JobStore(store.sf, max_attempts=int(os.getenv("JOB_MAX_ATTEMPTS", "5")))
    stopping = False

    def stop(signum: int, frame: FrameType | None) -> None:
        nonlocal stopping
        stopping = True
        log(f"sinal {signum}: encerrando após o job atual")

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    from src.agent.wiring import Agent
    from src.alerts.service import FailureStreak, build_alert_service
    from src.indexing.wiring import Indexing
    from src.agent.settings import mode_source
    from src.llm.config import build_chat_gateway, build_gateway, load_llm_config
    from src.llm.recorder import DbCallRecorder

    # one gateway per process: free-tier pacing is shared by every job kind;
    # the panel can switch free/fast at runtime (app_settings.llm_mode)
    gateway = build_gateway(
        DbCallRecorder(store.sf), mode_source(store.sf, load_llm_config().mode)
    )
    chat_gateway = build_chat_gateway(
        DbCallRecorder(store.sf), mode_source(store.sf, load_llm_config().mode)
    )
    agent = Agent(store, jobs, gateway, chat_gateway)
    indexing = Indexing(store, jobs, gateway)
    alerts = build_alert_service(store)
    runs = FailureStreak(
        AGENT_FAILURE_STREAK,
        lambda n, err: alerts.alert(
            "agent_failures", f"{n} execuções do agente falharam em seguida", err
        ),
    )
    handlers: dict[JobKind, Handler] = {
        "chat_turn": agent.chat_turn.handle,
        "process_email": runs.guard(agent.process_email.handle),
        "execute_action": agent.execute_action.handle,
        "index_email": indexing.indexer.handle,
        "index_attachment": indexing.documents.handle,
        "backfill_attachment": indexing.documents.handle,
        "backfill_page": indexing.backfill.handle,
    }

    def on_dead(job: ClaimedJob, error: str) -> None:
        log(f"job {job.id} ({job.payload.kind}) foi para dead-letter: {error[:200]}")
        if isinstance(job.payload, ExecuteActionJob):
            agent.approvals.mark_failed(job.payload.action_id, f"desistiu após retries: {error}")
        if isinstance(job.payload, ChatTurnJob):
            agent.chat_store.fail(job.payload.message_id, "Erro interno; tente de novo.")
        alerts.alert(
            "job_dead",
            f"job {job.id} ({job.payload.kind}) desistiu após {job.attempts} tentativas",
            f"{job.payload.model_dump_json()}\n\n{error}",
        )

    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    # Chat has its own lane so a turn never waits behind backfill/indexing;
    # both lanes share the gateway (and so the free-tier pacing).
    chat_lane = Worker(
        jobs,
        handlers,
        on_dead=on_dead,
        poll_s=float(os.getenv("CHAT_POLL_S", "1")),
        worker_id=f"{worker_id}:chat",
        heartbeat=None,
        kinds=CHAT_KINDS,
        housekeeping=False,
    )
    worker = Worker(
        jobs,
        handlers,
        on_dead=on_dead,
        poll_s=float(os.getenv("WORKER_POLL_S", "5")),
        worker_id=worker_id,
        heartbeat=HEARTBEAT,
        stale_after=timedelta(minutes=int(os.getenv("WORKER_STALE_MIN", "15"))),
        exclude=CHAT_KINDS,
    )
    lane = threading.Thread(
        target=chat_lane.run_forever, kwargs={"stop": lambda: stopping}, name="chat-lane",
        daemon=True,
    )
    lane.start()
    worker.run_forever(stop=lambda: stopping)
    lane.join(timeout=60)


if __name__ == "__main__":
    main()
