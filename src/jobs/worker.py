"""Worker loop: claim one job at a time, dispatch by kind, record the outcome.

Single process, sequential: the LLM gateway paces free-tier calls in-process,
so parallel workers would only fight over the same quota.
"""
import time
from collections.abc import Callable, Mapping
from datetime import timedelta
from pathlib import Path
from typing import Protocol

from src.db.scope import account_scope
from src.jobs.store import JobStore
from src.jobs.types import ClaimedJob, JobKind
from src.llm.errors import NoLLMKeyError, LLMWaitError
from src.utils.console import log

STALE_CHECK_EVERY_S = 600
# A job of an account without a Gemini key waits for the key: no attempt is spent.
NO_KEY_RECHECK_S = 900


class Deferred(Exception):
    """Raised by a handler to retry later without spending an attempt
    (quota exhausted, upstream overloaded)."""

    def __init__(self, seconds: int, reason: str) -> None:
        super().__init__(reason)
        self.seconds = seconds
        self.reason = reason


class Handler(Protocol):
    def __call__(self, job: ClaimedJob) -> None: ...


class Worker:
    def __init__(
        self,
        store: JobStore,
        handlers: Mapping[JobKind, Handler],
        *,
        on_dead: Callable[[ClaimedJob, str], None],
        poll_s: float,
        sleep: Callable[[float], None] = time.sleep,
        worker_id: str,
        heartbeat: Path | None,
        stale_after: timedelta = timedelta(minutes=15),
        clock: Callable[[], float] = time.monotonic,
        kinds: frozenset[JobKind] | None = None,
        exclude: frozenset[JobKind] = frozenset(),
        housekeeping: bool = True,
    ) -> None:
        """`kinds`/`exclude` pick this lane's jobs; only the housekeeping lane
        returns orphaned/stale jobs to the queue (one per process)."""
        self.store = store
        self.handlers = handlers
        self.on_dead = on_dead
        self.poll_s = poll_s
        self._sleep = sleep
        self.worker_id = worker_id
        self.heartbeat = heartbeat
        self.stale_after = stale_after
        self._clock = clock
        self.kinds = kinds
        self.exclude = exclude
        self.housekeeping = housekeeping

    def _beat(self) -> None:
        if self.heartbeat is not None:
            self.heartbeat.touch()

    def run_once(self) -> bool:
        """Process one job; False when the queue had nothing ready."""
        job = self.store.claim(self.worker_id, kinds=self.kinds, exclude=self.exclude)
        if job is None:
            return False
        with account_scope(job.account_id):
            return self._run(job)

    def _run(self, job: ClaimedJob) -> bool:
        kind = job.payload.kind
        handler = self.handlers.get(kind)
        try:
            if handler is None:
                raise LookupError(f"sem handler para {kind}")
            handler(job)
        except Deferred as d:
            self.store.defer(job.id, d.seconds, d.reason)
            log(f"job {job.id} ({kind}) adiado {d.seconds}s: {d.reason[:200]}")
            return True
        except LLMWaitError as w:  # the key must rest: wait in the queue, not in the lane
            self.store.defer(job.id, w.seconds, str(w))
            log(f"job {job.id} ({kind}) adiado {w.seconds}s: {str(w)[:200]}")
            return True
        except NoLLMKeyError as exc:
            self.store.defer(job.id, NO_KEY_RECHECK_S, str(exc))
            log(f"job {job.id} ({kind}) aguarda a chave do Gemini da conta {job.account_id}")
            return True
        except Exception as exc:  # noqa: BLE001 - every failure goes to the queue
            error = f"{type(exc).__name__}: {exc}"
            outcome = self.store.fail(job.id, error)
            log(f"job {job.id} ({kind}) falhou [{outcome}]: {error[:200]}")
            if outcome == "dead":
                try:
                    self.on_dead(job, error)
                except Exception as alert_exc:  # noqa: BLE001
                    log(f"on_dead falhou: {alert_exc}")
            return True
        self.store.complete(job.id)
        return True

    def run_forever(self, stop: Callable[[], bool] = lambda: False) -> None:
        last_stale_check: float | None = None
        log(f"worker {self.worker_id} no ar")
        if self.housekeeping:
            orphans = self.store.requeue_orphans()
            if orphans:
                log(f"{orphans} job(s) interrompido(s) voltaram para a fila")
        while not stop():
            now = self._clock()
            due = last_stale_check is None or now - last_stale_check >= STALE_CHECK_EVERY_S
            if self.housekeeping and due:
                n = self.store.requeue_stale(self.stale_after)
                if n:
                    log(f"{n} job(s) travado(s) voltaram para a fila")
                last_stale_check = now
            self._beat()
            if not self.run_once():
                self._sleep(self.poll_s)
