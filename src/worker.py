"""Agent worker process: `python -m src.worker`.

Consumes the Postgres job queue until SIGTERM/SIGINT. One LLM gateway per
process (free-mode pacing lives in it).
"""
import os
import signal
import socket
from datetime import timedelta
from pathlib import Path
from types import FrameType

from dotenv import load_dotenv

from src.db.store import Store
from src.jobs.store import JobStore
from src.jobs.types import ClaimedJob, JobKind
from src.jobs.worker import Handler, Worker
from src.utils.console import log

HEARTBEAT = Path(os.getenv("WORKER_HEARTBEAT", "/tmp/worker-heartbeat"))


def build_handlers(store: Store, jobs: JobStore) -> dict[JobKind, Handler]:
    """Job kind -> handler."""
    from src.agent.wiring import Agent

    agent = Agent(store, jobs)
    return {"process_email": agent.process_email.handle}


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

    def on_dead(job: ClaimedJob, error: str) -> None:
        log(f"job {job.id} ({job.payload.kind}) foi para dead-letter: {error[:200]}")

    worker = Worker(
        jobs,
        build_handlers(store, jobs),
        on_dead=on_dead,
        poll_s=float(os.getenv("WORKER_POLL_S", "5")),
        worker_id=f"{socket.gethostname()}:{os.getpid()}",
        heartbeat=HEARTBEAT,
        stale_after=timedelta(minutes=int(os.getenv("WORKER_STALE_MIN", "15"))),
    )
    worker.run_forever(stop=lambda: stopping)


if __name__ == "__main__":
    main()
