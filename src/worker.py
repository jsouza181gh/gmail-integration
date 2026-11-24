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
BACKFILL_KINDS: frozenset[JobKind] = frozenset({"backfill_page", "backfill_attachment"})


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
    from src.alerts.service import FailureStreak, build_alert_service, gmail_revoked_hook
    from src.gmail.clients import GmailClients
    from src.indexing.wiring import Indexing
    from src.agent.settings import mode_source, turbo_source
    from src.llm.backfill import BackfillLLM
    from src.llm.config import (
        build_backfill_gateway,
        build_chat_gateway,
        build_gateway,
        load_keys,
        load_llm_config,
    )
    from src.llm.recorder import DbCallRecorder

    # one gateway per key and use: free-tier pacing and quota memory live in
    # them; the panel can switch free/fast at runtime (app_settings.llm_mode)
    mode = mode_source(store.sf, load_llm_config().mode)
    gateway = build_gateway(DbCallRecorder(store.sf), mode)
    chat_gateway = build_chat_gateway(DbCallRecorder(store.sf), mode)
    clients = GmailClients(store.sf)
    agent = Agent(store, jobs, gateway, chat_gateway, clients)
    indexing = Indexing(store, jobs, gateway, clients)
    # App and new mail first: the history backfill runs on its own key (and in
    # turbo borrows the app key only while it is idle). With one key the
    # backfill shares the app gateway, so a single quota memory exists.
    backfill_llm = (
        BackfillLLM(build_backfill_gateway(DbCallRecorder(store.sf), mode), gateway,
                    turbo_source(store.sf))
        if load_keys().separate
        else BackfillLLM(gateway, None, lambda: False)
    )
    backfill_indexing = Indexing(store, jobs, backfill_llm, clients)
    alerts = build_alert_service(store, clients)
    clients.on_revoked = gmail_revoked_hook(alerts)
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
        "backfill_attachment": backfill_indexing.documents.handle,
        "backfill_page": backfill_indexing.backfill.handle,
    }

    def on_dead(job: ClaimedJob, error: str) -> None:
        log(f"job {job.id} ({job.payload.kind}) foi para dead-letter: {error[:200]}")
        if isinstance(job.payload, ExecuteActionJob):
            agent.approvals.mark_failed(job.payload.action_id, f"desistiu após retries: {error}")
            agent.card_waiter.on_action_settled(job.payload.action_id)
        if isinstance(job.payload, ChatTurnJob):
            agent.chat_store.fail(job.payload.message_id, "Erro interno; tente de novo.")
        alerts.alert(
            "job_dead",
            f"job {job.id} ({job.payload.kind}) desistiu após {job.attempts} tentativas",
            f"{job.payload.model_dump_json()}\n\n{error}",
        )

    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    # Chat has its own lane so a turn never waits behind indexing or the backfill.
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
    # The history backfill has its own lane: a page (25 emails, retries) never
    # holds up the triage of mail that just arrived.
    backfill_lane = Worker(
        jobs,
        handlers,
        on_dead=on_dead,
        poll_s=float(os.getenv("WORKER_POLL_S", "5")),
        worker_id=f"{worker_id}:backfill",
        heartbeat=None,
        kinds=BACKFILL_KINDS,
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
        exclude=CHAT_KINDS | BACKFILL_KINDS,
    )
    lane = threading.Thread(
        target=chat_lane.run_forever, kwargs={"stop": lambda: stopping}, name="chat-lane",
        daemon=True,
    )
    lane.start()
    backfill = threading.Thread(
        target=backfill_lane.run_forever, kwargs={"stop": lambda: stopping},
        name="backfill-lane", daemon=True,
    )
    backfill.start()
    worker.run_forever(stop=lambda: stopping)
    lane.join(timeout=60)
    backfill.join(timeout=60)


if __name__ == "__main__":
    main()
