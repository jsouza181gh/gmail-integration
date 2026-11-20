"""/api/agent: everything the agent's behaviour depends on, versioned/audited."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select

from src.agent.config import DbAgentConfigs
from src.agent.kb import KbAdmin, KbRow, KbScope
from src.agent.policy import PolicyAdmin, PolicyRow
from src.agent.rules import RuleAdmin
from src.agent.settings import LlmMode, RuntimeSettings, audit
from src.agent.types import PolicyDecision, PolicyTarget, Trigger
from src.api.deps import get_deps, require_user
from src.db.models import ConfigAudit
from src.db.rows import AgentConfigRow, RuleRow
from src.llm.types import Schema

router = APIRouter(prefix="/api/agent", dependencies=[Depends(require_user)], tags=["agent"])


class ConfigIn(Schema):
    decide_prompt: str
    chat_prompt: str | None = None
    max_turns: int


class ConfigsOut(Schema):
    active: AgentConfigRow | None
    versions: list[AgentConfigRow]


class PolicyIn(Schema):
    target: PolicyTarget
    trigger: Trigger
    policy: PolicyDecision


class RuleIn(Schema):
    name: str
    action: str
    sender_address: str | None = None
    sender_domain: str | None = None
    category: str | None = None
    subject_contains: str | None = None
    unless_document: bool = False


class KbIn(Schema):
    scope: KbScope
    text: str


class SettingsIo(Schema):
    llm_mode: LlmMode | None


class AuditOut(Schema):
    id: int
    entity: str
    entity_id: str
    channel: str
    before: str | None
    after: str | None
    created_at: datetime


# --- config versions ---------------------------------------------------------------

@router.get("/config")
def config(request: Request) -> ConfigsOut:
    configs = DbAgentConfigs(get_deps(request).sf)
    return ConfigsOut(active=configs.active(), versions=configs.versions())


@router.post("/config")
def new_config(body: ConfigIn, request: Request) -> AgentConfigRow:
    deps = get_deps(request)
    try:
        return DbAgentConfigs(deps.sf, deps.clock).create(
            body.decide_prompt, body.max_turns, chat_prompt=body.chat_prompt
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/config/{config_id}/activate", status_code=204)
def activate(config_id: int, request: Request) -> None:
    deps = get_deps(request)
    if not DbAgentConfigs(deps.sf, deps.clock).activate(config_id):
        raise HTTPException(404, "versão não encontrada")


# --- policies --------------------------------------------------------------------

@router.get("/policies")
def policies(request: Request) -> list[PolicyRow]:
    return PolicyAdmin(get_deps(request).sf).list()


@router.put("/policies", status_code=204)
def set_policy(body: PolicyIn, request: Request) -> None:
    deps = get_deps(request)
    try:
        PolicyAdmin(deps.sf, deps.clock).set(body.target, body.trigger, body.policy)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


# --- rules -------------------------------------------------------------------------

def _rule_audit(request: Request, rule_id: int, before: str | None, after: str) -> None:
    deps = get_deps(request)
    with deps.sf.begin() as s:
        audit(s, "rule", rule_id, "ui", before, after, deps.clock())


def _rule(admin: RuleAdmin, rule_id: int) -> RuleRow:
    row = admin.get(rule_id)
    if row is None:
        raise HTTPException(404, "regra não encontrada")
    return row


@router.get("/rules")
def rules(request: Request) -> list[RuleRow]:
    return list(RuleAdmin(get_deps(request).sf).list())


@router.post("/rules")
def add_rule(body: RuleIn, request: Request) -> RuleRow:
    deps = get_deps(request)
    admin = RuleAdmin(deps.sf, deps.clock)
    try:
        rule_id = admin.add(**body.model_dump(), source="ui")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    row = _rule(admin, rule_id)
    _rule_audit(request, rule_id, None, f"{row.name}: {row.action} ({row.mode})")
    return row


@router.post("/rules/{rule_id}/promote")
def promote(rule_id: int, request: Request) -> RuleRow:
    admin = RuleAdmin(get_deps(request).sf)
    before = _rule(admin, rule_id)
    try:
        admin.promote(rule_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    _rule_audit(request, rule_id, before.mode, "auto")
    return _rule(admin, rule_id)


@router.post("/rules/{rule_id}/disable")
def disable(rule_id: int, request: Request) -> RuleRow:
    admin = RuleAdmin(get_deps(request).sf)
    _rule(admin, rule_id)
    admin.disable(rule_id)
    _rule_audit(request, rule_id, "ativa", "inativa")
    return _rule(admin, rule_id)


# --- knowledge base ------------------------------------------------------------------

@router.get("/kb")
def kb(request: Request, scope: str | None = None) -> list[KbRow]:
    return KbAdmin(get_deps(request).sf).list(scope)


@router.post("/kb")
def add_kb(body: KbIn, request: Request) -> KbRow:
    deps = get_deps(request)
    try:
        return KbAdmin(deps.sf, deps.clock).add(body.scope, body.text)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/kb/{entry_id}", status_code=204)
def deactivate_kb(entry_id: int, request: Request) -> None:
    deps = get_deps(request)
    if not KbAdmin(deps.sf, deps.clock).deactivate(entry_id):
        raise HTTPException(404, "entrada não encontrada ou já inativa")


# --- runtime settings and audit ------------------------------------------------------

@router.get("/settings")
def settings(request: Request) -> SettingsIo:
    return SettingsIo(llm_mode=RuntimeSettings(get_deps(request).sf).llm_mode())


@router.put("/settings", status_code=204)
def set_settings(body: SettingsIo, request: Request) -> None:
    deps = get_deps(request)
    if body.llm_mode is not None:
        RuntimeSettings(deps.sf, deps.clock).set_llm_mode(body.llm_mode)


@router.get("/audit")
def audit_log(request: Request, limit: int = Query(200, ge=1, le=1000)) -> list[AuditOut]:
    with get_deps(request).sf() as s:
        return [
            AuditOut(id=a.id, entity=a.entity, entity_id=a.entity_id, channel=a.channel,
                     before=a.before, after=a.after, created_at=a.created_at)
            for a in s.scalars(select(ConfigAudit).order_by(ConfigAudit.id.desc()).limit(limit))
        ]

