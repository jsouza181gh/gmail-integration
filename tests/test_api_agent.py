import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.agent.settings import RuntimeSettings, mode_source
from src.db.models import AgentConfig, ConfigAudit, ToolPolicy
from tests.api_fakes import Api


@pytest.fixture
def api(session_factory: sessionmaker[Session]) -> Api:
    with session_factory.begin() as s:
        s.add(AgentConfig(version=1, active=True, decide_prompt="v1", chat_prompt="c", max_turns=4))
        s.add(ToolPolicy(target="trash", trigger="webhook", policy="suggest"))
        s.add(ToolPolicy(target="block_sender", trigger="webhook", policy="suggest"))
    a = Api(session_factory)
    a.login()
    return a


def audit(sf: sessionmaker[Session]) -> list[tuple[str, str]]:
    with sf() as s:
        return [(a.entity, a.channel) for a in s.scalars(select(ConfigAudit).order_by(ConfigAudit.id))]


def test_config_version_and_rollback(api: Api) -> None:
    c = api.client
    r = c.post("/api/agent/config", json={"decide_prompt": "v2 prompt", "max_turns": 3})
    assert r.status_code == 200 and r.json()["version"] == 2
    cfg = c.get("/api/agent/config").json()
    assert cfg["active"]["version"] == 2 and [v["version"] for v in cfg["versions"]] == [2, 1]
    v1 = next(v["id"] for v in cfg["versions"] if v["version"] == 1)
    assert c.post(f"/api/agent/config/{v1}/activate").status_code == 204
    assert c.get("/api/agent/config").json()["active"]["decide_prompt"] == "v1"
    assert c.post("/api/agent/config", json={"decide_prompt": "x", "max_turns": 99}).status_code == 422
    assert audit(api.sf) == [("agent_config", "ui"), ("agent_config", "ui")]


def test_policy_guard(api: Api) -> None:
    c = api.client
    assert c.put("/api/agent/policies", json={"target": "trash", "trigger": "webhook", "policy": "auto"}).status_code == 204
    assert c.put("/api/agent/policies", json={"target": "block_sender", "trigger": "webhook", "policy": "auto"}).status_code == 422
    assert c.put("/api/agent/policies", json={"target": "nope", "trigger": "webhook", "policy": "auto"}).status_code == 422
    pols = {(p["target"], p["trigger"]): p["policy"] for p in c.get("/api/agent/policies").json()}
    assert pols[("trash", "webhook")] == "auto" and pols[("block_sender", "webhook")] == "suggest"
    with api.sf() as s:
        row = s.scalars(select(ConfigAudit)).one()
    assert (row.entity, row.before, row.after) == ("tool_policy", "suggest", "auto")


def test_rules_add_promote_disable(api: Api) -> None:
    c = api.client
    r = c.post("/api/agent/rules", json={"name": "lojas", "action": "trash", "sender_domain": "loja.com"})
    rule_id = r.json()["id"]
    assert r.json()["mode"] == "suggest"
    assert c.post(f"/api/agent/rules/{rule_id}/promote").json()["mode"] == "auto"
    assert c.post(f"/api/agent/rules/{rule_id}/disable").json()["active"] is False
    assert c.post("/api/agent/rules", json={"name": "x", "action": "trash"}).status_code == 422
    blk = c.post("/api/agent/rules", json={"name": "b", "action": "block_sender", "sender_domain": "x.com"}).json()
    assert c.post(f"/api/agent/rules/{blk['id']}/promote").status_code == 422
    assert [r["name"] for r in c.get("/api/agent/rules").json()] == ["lojas", "b"]
    assert [e for e, _ in audit(api.sf)] == ["rule", "rule", "rule", "rule"]


def test_kb_add_and_deactivate(api: Api) -> None:
    c = api.client
    kb = c.post("/api/agent/kb", json={"scope": "decision", "text": "nunca apagar e-mails da 42"}).json()
    assert kb["source"] == "ui" and kb["active"] is True
    assert c.delete(f"/api/agent/kb/{kb['id']}").status_code == 204
    assert c.get("/api/agent/kb", params={"scope": "decision"}).json()[0]["active"] is False
    assert c.post("/api/agent/kb", json={"scope": "x", "text": "y"}).status_code == 422


def test_settings_and_gateway_mode_source(api: Api) -> None:
    c = api.client
    assert c.get("/api/agent/settings").json() == {"llm_mode": None}
    assert c.put("/api/agent/settings", json={"llm_mode": "fast"}).status_code == 204
    assert c.get("/api/agent/settings").json() == {"llm_mode": "fast"}
    source = mode_source(api.sf, "free", ttl_s=0)
    assert source() == "fast"
    RuntimeSettings(api.sf).set_llm_mode("free")
    assert source() == "free"
    assert c.get("/api/agent/audit").json()[0]["entity"] == "settings"
