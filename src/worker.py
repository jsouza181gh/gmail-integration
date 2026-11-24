"""Agent worker process: `python -m src.worker`.

Consumes the Postgres job queue until SIGTERM/SIGINT. LLM gateways are cached
per Gemini key (free-mode pacing and quota memory live in them).
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
    from src.alerts.service import FailureStreak, build_alert_service
    from src.indexing.wiring import Indexing
    from src.llm.accounts import AccountLLMs
    from src.llm.config import env_account_keys

    # LLM calls resolve the current account's own Gemini keys (BYOK): gateways
    # (free-tier pacing, cooldown and quota memory) are cached per key. Only the
    # admin account may fall back to the .env keys, until the import CLI runs.
    llms = AccountLLMs(store.sf, env_keys=env_account_keys)
    agent = Agent(store, jobs, llms.app, llms.chat)
    indexing = Indexing(store, jobs, llms.app)
    # App and new mail first: the history backfill runs on the account's backfill
    # key (and in turbo borrows its app key only while it is idle).
    backfill_indexing = Indexing(store, jobs, llms.backfill)
    alerts = build_alert_service(store)
    runs = FailureStreak(
        AGENT_FAILURE_STREAK,
        lambda n, err: alerts.alert(
            "agent_failures", f"{n} execuções do agente falharam em seguida", err
        ),
    )
    def keyed(handler: Handler) -> Handler:
        """An account without a key waits (the job is deferred, no attempt spent)
        before the job opens runs or fetches anything."""

        def run(job: ClaimedJob) -> None:
            llms.resolver.keys(job.account_id)
            handler(job)

        return run

    handlers: dict[JobKind, Handler] = {
        "chat_turn": agent.chat_turn.handle,  # a missing key becomes the chat's own message
        "process_email": keyed(runs.guard(agent.process_email.handle)),
        "execute_action": agent.execute_action.handle,
        "index_email": keyed(indexing.indexer.handle),
        "index_attachment": keyed(indexing.documents.handle),
        "backfill_attachment": keyed(backfill_indexing.documents.handle),
        "backfill_page": keyed(backfill_indexing.backfill.handle),
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
