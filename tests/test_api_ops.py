from datetime import timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import (
    AgentConfig,
    AgentRun,
    AgentStep,
    Email,
    Job,
    LlmCall,
    Notification,
    UserLlmKey,
    RunEmail,
)
from src.indexing.backfill import RUN_QUERY
from src.indexing.store import IndexStore
from src.jobs.types import BackfillPageJob, ProcessEmailJob
from src.llm.keys import save_keys
from tests.api_fakes import SETTINGS, Api
from tests.fakes import FakeService


def gmail() -> FakeService:
    return FakeService(users__getProfile=lambda **kw: {"emailAddress": "x", "messagesTotal": 1000,
                                                "historyId": "1"})


@pytest.fixture
def api(session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> Api:
    monkeypatch.setenv("APP_SECRET", SETTINGS.app_secret)
    a = Api(session_factory, gmail=gmail)
    save_keys(session_factory, 1, app_key="app-key", backfill_key=None)  # the user's own key
    a.login()
    return a


def finished_pages(api: Api, run_id: int, ages_h: list[float]) -> None:
    now = api.clock()
    with api.sf.begin() as s:
        for i, age in enumerate(ages_h):
            s.add(Job(kind="backfill_page", priority=0, status="done", attempts=1, max_attempts=5,
                      run_after=now, idempotency_key=f"backfill_page:{run_id}:{i}", run_id=run_id,
                      page_no=i, created_at=now, finished_at=now - timedelta(hours=age)))


def test_backfill_status_percent_eta_and_pause(api: Api) -> None:
    run = IndexStore(api.sf).backfill_run(RUN_QUERY)
    with api.sf.begin() as s:
        for i in range(250):
            s.add(Email(id=f"b{i}", attempts=0, category="Social", indexed_at=api.clock()))
        s.flush()
        s.add_all(RunEmail(run_id=run.id, email_id=f"b{i}") for i in range(250))
    finished_pages(api, run.id, [0.5] * 4 + [5.0] * 6)  # 4 in the last hour, 10 in 24h
    api.deps.jobs.enqueue(BackfillPageJob(run_id=run.id, page_no=10))
    with api.sf.begin() as s:
        job = s.query(Job).filter(Job.page_no == 10).one()
        job.run_after = api.clock() + timedelta(minutes=20)  # deferred by quota
    st = api.client.get("/api/backfill").json()
    assert st["progress"]["emails"] == 250 and st["mailbox_total"] == 1000
    assert st["percent"] == 25.0
    assert (st["pages_last_hour"], st["pages_last_day"]) == (4, 10)
    # 750 emails left = 30 pages; 10 pages over the 5 h actually worked = 2/h
    assert st["eta_hours"] == pytest.approx(15.0)
    assert st["deferred_until"] is not None


def test_backfill_start_is_idempotent(api: Api) -> None:
    a = api.client.post("/api/backfill/start").json()
    b = api.client.post("/api/backfill/start").json()
    assert a["progress"]["run_id"] == b["progress"]["run_id"]
    assert b["progress"]["jobs"]["backfill_page"]["queued"] == 1


def test_runs_detail_with_steps_and_calls(api: Api) -> None:
    now = api.clock()
    with api.sf.begin() as s:
        s.add(AgentConfig(id=1, version=1, active=True, decide_prompt="p", chat_prompt="c", max_turns=4))
        s.add(Email(id="e1", attempts=0))
        s.flush()
        s.add(AgentRun(id=7, trigger="webhook", email_id="e1", config_id=1, status="done", started_at=now))
        s.flush()
        s.add(AgentStep(run_id=7, position=0, kind="tool", name="classify_email", source="planned",
                        status="ok", started_at=now))
        s.add(LlmCall(purpose="classify", model="m", attempt=1, status="ok", latency_ms=900,
                      input_tokens=10, output_tokens=5, total_tokens=15, cached_input_tokens=0,
                      reasoning_tokens=0, audio_input_tokens=0, agent_run_id=7))
    runs = api.client.get("/api/runs").json()
    assert [r["id"] for r in runs] == [7]
    d = api.client.get("/api/runs/7").json()
    assert [st["name"] for st in d["steps"]] == ["classify_email"]
    assert d["llm_calls"][0]["total_tokens"] == 15
    assert api.client.get("/api/runs/99").status_code == 404


def test_jobs_and_requeue(api: Api) -> None:
    with api.sf.begin() as s:
        s.add(Email(id="e1", attempts=0))
    api.deps.jobs.enqueue(ProcessEmailJob(email_id="e1"))
    job = api.deps.jobs.claim("w")
    assert job is not None
    with api.sf.begin() as s:
        s.get_one(Job, job.id).attempts = 5  # last attempt
    assert api.deps.jobs.fail(job.id, "boom") == "dead"
    body = api.client.get("/api/jobs").json()
    assert body["counts"]["process_email"]["dead"] == 1 and body["dead"][0]["id"] == job.id
    assert api.client.post(f"/api/jobs/{job.id}/requeue").status_code == 204
    assert api.client.post(f"/api/jobs/{job.id}/requeue").status_code == 409


def test_usage_by_day(api: Api) -> None:
    with api.sf.begin() as s:
        for status in ("ok", "ok", "error"):
            s.add(LlmCall(purpose="classify", model="m", attempt=1, status=status, latency_ms=1,
                          input_tokens=100, output_tokens=10, total_tokens=110, cached_input_tokens=0,
                          reasoning_tokens=0, audio_input_tokens=0))
    days = api.client.get("/api/usage").json()
    assert len(days) == 1
    d = days[0]
    assert (d["purpose"], d["calls"], d["errors"], d["input_tokens"]) == ("classify", 3, 1, 300)


def test_security_lists(api: Api) -> None:
    now = api.clock()
    with api.sf.begin() as s:
        s.add(Notification(kind="job_dead", subject="x", body="y", status="sent", created_at=now))
    kinds = [e["kind"] for e in api.client.get("/api/security/events").json()]
    assert "login_ok" in kinds
    assert api.client.get("/api/security/blocks").json() == {"global_lock": False, "ips": []}
    assert api.client.get("/api/notifications").json()[0]["kind"] == "job_dead"


def test_backfill_pause_and_resume(api: Api) -> None:
    from datetime import timedelta

    from sqlalchemy import select

    from src.db.models import ConfigAudit, Job

    c = api.client
    assert c.get("/api/backfill").json()["paused"] is False
    c.post("/api/backfill/start")
    paused = c.post("/api/backfill/pause")
    assert paused.status_code == 200 and paused.json()["paused"] is True
    with api.sf.begin() as s:  # the worker deferred the page while paused
        job = s.scalars(select(Job).where(Job.kind == "backfill_page")).one()
        job.run_after = api.clock() + timedelta(minutes=5)
        job.last_error = "backfill pausado"
    resumed = c.post("/api/backfill/resume").json()
    assert resumed["paused"] is False and resumed["deferred_until"] is None
    with api.sf() as s:
        assert s.scalars(select(Job.run_after)).one().replace(tzinfo=None) <= api.clock().replace(tzinfo=None)
        assert [a.entity for a in s.scalars(select(ConfigAudit))] == ["settings", "settings"]


def test_backfill_resume_restarts_a_dead_chain(api: Api) -> None:
    from sqlalchemy import func, select

    from src.db.models import Job

    api.client.post("/api/backfill/pause")
    assert api.client.post("/api/backfill/resume").status_code == 200
    with api.sf() as s:
        assert s.scalar(select(func.count()).select_from(Job).where(Job.kind == "backfill_page")) == 1


def test_backfill_turbo_toggle(api: Api) -> None:
    c = api.client
    status = c.get("/api/backfill").json()
    assert status["turbo"] is False and status["turbo_available"] is False
    save_keys(api.sf, 1, app_key="app-key", backfill_key="bf-key")
    api.deps.llm_keys.invalidate(1)
    on = c.post("/api/backfill/turbo", json={"on": True})
    assert on.status_code == 200 and on.json()["turbo"] is True and on.json()["turbo_available"] is True
    assert c.post("/api/backfill/turbo", json={"on": False}).json()["turbo"] is False


def test_backfill_start_needs_the_users_own_key(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    # another test may have loaded the real .env: no admin fallback to it here
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY_BACKFILL", raising=False)
    with api.sf.begin() as s:
        s.query(UserLlmKey).delete()
    api.deps.llm_keys.invalidate(1)
    r = api.client.post("/api/backfill/start")
    assert r.status_code == 409 and "chave" in r.json()["detail"]
