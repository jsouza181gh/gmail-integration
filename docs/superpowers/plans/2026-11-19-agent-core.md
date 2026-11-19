# Núcleo do agente (subprojeto 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (o João pediu execução inline, sem subagentes). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Todo e-mail novo vira um job na fila do Postgres; um worker roda o agente sobre ele (classifica, aplica regras, aplica labels `IA/*`, decide ações), registra cada passo, grava ações sensíveis como sugestões para aprovação via CLI, executa as aprovadas e avisa por e-mail quando algo dá errado.

**Architecture:** `src/jobs/` é a fila (`SKIP LOCKED`) + o loop do worker. `src/agent/` é o agente: `AgentContext` (imutável, por run) e `AgentState` (evolui por run) tipados em Pydantic; cada tool é `@tool` do LangChain com `runtime: ToolRuntime[<Fatia>Context, <Fatia>State]` e recebe **só a fatia** do contexto/estado de que precisa, projetada pelo registro de tools; tools nunca mutam estado — devolvem um `StateUpdate` tipado que o loop aplica. O fluxo de um e-mail é um **pipeline planejado** (classificar → regras → labels → histórico do remetente, todos como chamadas de tool feitas pelo orquestrador) seguido de **um loop agêntico curto** de decisão (modelo + tools, teto de turnos). Política de ações resolvida no runtime, nunca na tool, com invariantes no código acima do banco.

**Tech Stack:** Python 3.12, LangChain 1.4 (`langchain.tools.ToolRuntime`, `langchain_core.tools.tool`, `bind_tools`), `langchain-google-genai`, SQLAlchemy 2, Alembic, Postgres 16 (SQLite nos testes), pytest, mypy strict.

**Spec:** `docs/superpowers/specs/2026-11-19-gmail-assistant-design.md` (Arquitetura, Política de ações, Regras e KB, Personalização e rastreio, Observabilidade e alertas, Subprojetos → 2).

## Global Constraints

- Herdadas do subprojeto 1, sem exceção: schema Pydantic frozen/extra-forbid como contrato entre módulos (nada de `dict` solto, `dataclass`, tupla); `typing.Any` proibido (mypy `strict` + `disallow_any_explicit` + plugin pydantic); **sem colunas JSON/JSONB**; LangChain, não LiteLLM; toda chamada de LLM passa por `LLMGateway`; conteúdo de e-mail entra como dado; nenhum dado pessoal em código/seed/docs; Conventional Commits em inglês com `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; migrações só aditivas; branch `feat/agent-core` a partir da `main`, PR no fim.
- **Tools [decidido]:** toda tool recebe `runtime: ToolRuntime[C, S]` (de `langchain.tools`), lê dependências em `runtime.context` e conhecimento acumulado em `runtime.state`; tools que consomem LLM pegam o gerador **e** o `CallContext` do `runtime.context`. `C`/`S` contêm só o que a tool usa.
- **Sem LangGraph no loop** (spec): `ToolRuntime` é só o tipo injetado; o loop próprio monta a instância e chama `tool.invoke(ToolCall)`.
- **Política [decidido]:** labels `IA/*` automáticas; marcar lido, estrela, arquivar, lixeira, bloquear remetente → sugeridas por padrão; `messages.delete` não existe como tool nem como método do executor; `block_sender` (filtro) **nunca** `auto`, nem por regra nem por `tool_policies`. Enviar/responder não entra neste subprojeto (vem com o chat, subprojeto 5).
- Regras de automação nascem `suggest`; `auto` só por comando explícito (`src.cli.rules promote`).
- Webhook nunca altera config/regras/políticas; o agente do webhook não tem tool para isso.
- Alertas: e-mail enviado pelo servidor ao próprio endereço (`watch_state.email_address`), assunto `[gmail-integration] …`, header `X-Gmail-Integration: alert`, label `Sistema/Alerta`; no máximo 1 por tipo a cada 15 min; o sync ignora esses e-mails.
- Env novas: `WORKER_POLL_S` (padrão `5`), `WORKER_STALE_MIN` (padrão `15`), `JOB_MAX_ATTEMPTS` (padrão `5`), `AGENT_MAX_TURNS` (seed de `agent_configs`, padrão `4`), `LABELS_DRY_RUN` (padrão vazio; `1` = labeler só loga, para verificação sem tocar o Gmail).
- `mypy.ini` `files =` ganha cada arquivo novo/modificado na task que o toca.

## Modelo do fluxo (o coração do plano)

```
webhook ─▶ sync (ingest) ─▶ jobs: process_email(email_id)        [idempotência "process_email:<id>"]
                                     │
worker ─ claim (SKIP LOCKED) ───────▶ ProcessEmailFlow
   agent_run(trigger=webhook, config_version)  ── cada item abaixo é um agent_step
   1. planned  classify_email          (tool LLM: state.email → Classified)
   2. planned  match_rules             (determinístico: classificação + remetente → RuleMatch[])
   3. planned  apply_labels            (tool Gmail: IA/<categoria>, IA/Relevante; política auto)
   4. planned  sender_history          (tool DB: volume, categorias, decisões passadas do remetente)
   5. se houve regra: propose_action por regra (mode da regra, limitado pelos invariantes) → fim
      senão: decide loop ─ até max_turns chamadas LLM:
               modelo vê: e-mail (como dado), classificação, histórico do remetente, KB (scope decision+classification)
               tools: get_email_body, propose_action
               termina quando responde sem tool_calls (texto = resumo da decisão)
   6. run.status = done | truncated | failed ; resumo, contadores
aprovação (CLI) ─▶ suggested_actions.status=approved ─▶ jobs: execute_action(action_id) ─▶ GmailActions
recusa/comentário ─▶ feedback ─▶ kb_entries(scope=decision, source=approval)
falhas (job dead, runs falhando em série, sync falhando) ─▶ notifications ─▶ e-mail de alerta
```

Por que assim: classificar, regras, labels e histórico não precisam de "decisão" do modelo — fazê-los como chamadas planejadas economiza turnos (free tier) e garante que todo e-mail sai classificado e etiquetado mesmo se o loop falhar. Mas eles passam **pelo mesmo executor de tools** (mesmo `ToolRuntime`, mesmo `StateUpdate`, mesmo `agent_step`), então no chat (subprojeto 5) o modelo chama as mesmas tools por conta própria. O estado acumulado (corpo já baixado, classificação, histórico) chega às tools seguintes via `runtime.state`, sem refetch. Na maioria dos e-mails o loop decide em **1 chamada** (tudo pré-carregado); `get_email_body` existe para quando o snippet não basta.

## Rulings de design (decididos por mim; mudam o que é construído se você discordar)

1. **`ToolRuntime[AgentContext]` literal vs. isolamento.** Se toda tool recebesse `ToolRuntime[AgentContext, AgentState]`, toda tool enxergaria tudo. Escolhi: `AgentContext`/`AgentState` são as raízes tipadas; cada tool declara `ToolRuntime[XContext, XState]` com fatias, e o registro (`AgentTool`) guarda as projeções `AgentContext → XContext` e `AgentState → XState`. Isolamento é real em runtime, não só no tipo. Custo se errado: trocar as anotações por `ToolRuntime[AgentContext, AgentState]` (mecânico).
2. **Dependências no contexto Pydantic:** campos tipados com `Protocol` `@runtime_checkable` + `arbitrary_types_allowed` (validação por `isinstance`). Sem `dataclass`.
3. **Args de tool e eventos de step em `agent_steps.detail` como texto** (JSON serializado só para auditoria, nunca lido de volta para lógica) — mesma natureza de `llm_call_messages.content`. Tabelas por tool seriam desproporcionais. Custo se errado: migração para tabelas filhas.
4. **KB sem embeddings neste subprojeto:** prompt de decisão leva todas as entradas ativas (`scope in {classification, decision}`); pgvector chega no subprojeto 3. Contadores RPD/TPM persistidos também ficam para o 3 (backfill é quem estoura cota).
5. **Regras avaliadas depois da classificação** (podem usar categoria). Pré-LLM barato vira relevante só no backfill (subprojeto 3).
6. **Rebaixamento `auto → suggest` ao desfazer** exige ler eventos de label do history; fica para depois (contadores já existem). Aviso de "anexo importante" no card idem (subprojeto 3).
7. **`block_sender` exige escopo `gmail.settings.basic`**: implementado, mas o executor falha com mensagem "reautorize" enquanto o token não tiver o escopo; o token atual continua válido (escopo obrigatório segue só `gmail.modify`).

## Review Focus

1. Mesmo e-mail enfileirado duas vezes (push duplicado / retry do job após crash no meio do run) → um job só (idempotência) e nenhuma sugestão duplicada (índice único parcial `(email_id, kind)` com `status='pending'`). Testes em Task 2 e Task 5.
2. Modelo chama tool inexistente, tool negada pela política, args inválidos ou repete a mesma chamada → `ToolMessage` de erro para o modelo, step com `status=error`, loop segue até `max_turns`; nunca exceção que derrube o run. Teste em Task 3.
3. `tool_policies` no banco marcando `block_sender` como `auto`, ou regra promovida com ação `block_sender` → continua `suggest` (invariante no código). Teste em Task 5.
4. Worker morre com job `running` → depois de `WORKER_STALE_MIN` volta a `queued`, sem perder tentativa extra além de 1. Teste em Task 2.
5. E-mail de alerta do próprio sistema chegando pelo webhook → não é ingerido nem gera job (sem loop de alertas). Teste em Task 8.

---

### Task 1: Gateway com tool calling + ligação ao agente

**Files:**
- Modify: `src/llm/types.py`, `src/llm/gateway.py`, `src/llm/recorder.py`, `src/db/models.py`
- Create: `alembic/versions/0004_llm_calls_agent_link.py` (só colunas em `llm_calls`; FKs para `agent_runs`/`agent_steps` entram na migração da Task 3, que cria as tabelas)
- Test: `tests/test_llm_gateway.py`, `tests/test_llm_recorder.py`

**Interfaces:**
- Produces (`src/llm/types.py`):
  - `ToolCallRequest(id: str, name: str, args: dict[str, JsonValue])` — `JsonValue` de `pydantic` (recursivo, sem `Any`).
  - Mensagens de conversa: `ChatMessage` ganha papel `"tool"` e campos opcionais `tool_calls: tuple[ToolCallRequest, ...] = ()` (só `ai`) e `tool_call_id: str | None = None` (só `tool`); validator garante a combinação.
  - `AssistantTurn(content: str, tool_calls: tuple[ToolCallRequest, ...], model: str, usage: TokenUsage)`.
  - `CallContext` ganha `agent_run_id: int | None = None`, `agent_step_id: int | None = None`.
  - `ToolCaller` (`Protocol`, `@runtime_checkable`): `def generate_turn(self, prompt: Prompt, tools: Sequence[BaseTool]) -> AssistantTurn`.
  - `ChatModelLike` ganha `def bind_tools(self, tools: Sequence[BaseTool]) -> ToolRunnable`; `ToolRunnable.invoke(input: list[BaseMessage], /) -> object` (validado como `AIMessage`).
  - `StructuredGenerator` passa a `@runtime_checkable`.
- `LLMGateway.generate_turn` reusa o mesmo laço de tentativa/backoff/registro de `generate_structured` (extrair `_attempts(prompt, call: Callable[[ChatModelLike, list[BaseMessage]], _Outcome])`); `response_text` do registro = texto + `name(args)` de cada tool call.
- `llm_calls` ganha `agent_run_id`, `agent_step_id` (Integer, nullable, index); `DbCallRecorder` grava a partir do `CallContext`.

- [ ] **Step 1: Failing tests**
  - `test_generate_turn_returns_tool_calls`: fake `bind_tools` devolve `AIMessage(content="", tool_calls=[{"id":"c1","name":"get_email_body","args":{"email_id":"a"}}], usage_metadata=...)` → `turn.tool_calls == (ToolCallRequest(id="c1", name="get_email_body", args={"email_id": "a"}),)` e `turn.usage.total_tokens == 15`.
  - `test_generate_turn_retries_transient_and_records_both`: `[RuntimeError("503 UNAVAILABLE"), AIMessage("ok")]` → 2 registros `error`,`ok`.
  - `test_tool_messages_round_trip_to_langchain`: prompt com `ai` com tool_calls + `tool` → fake recebe `AIMessage.tool_calls[0]["id"] == "c1"` e `ToolMessage.tool_call_id == "c1"`.
  - `test_chat_message_rejects_tool_calls_on_human`: `ChatMessage(role="human", content="x", tool_calls=(...))` → `ValidationError`.
  - `test_recorder_links_agent_run_and_step` (recorder): `CallContext(agent_run_id=1, agent_step_id=2)` → linha com os dois valores.
- [ ] **Step 2: Rodar** `pytest tests/test_llm_gateway.py tests/test_llm_recorder.py -q` → Expected: FAIL (`AttributeError: generate_turn` / campos ausentes).
- [ ] **Step 3: Implementar** conforme Interfaces; migração 0004 adiciona as duas colunas.
- [ ] **Step 4: Rodar** os testes + `pytest tests/test_migrations.py -q` + `mypy` → Expected: PASS, mypy `Success`.
- [ ] **Step 5: Commit** `feat(llm): tool-calling turns and agent run/step linkage on llm_calls`

---

### Task 2: Fila de jobs + worker

**Files:**
- Create: `src/jobs/__init__.py`, `src/jobs/types.py`, `src/jobs/store.py`, `src/jobs/worker.py`, `src/worker.py` (entrypoint `python -m src.worker`), `alembic/versions/0005_jobs.py`
- Modify: `src/db/models.py` (`Job`)
- Test: `tests/test_jobs.py`

**Interfaces:**
- `jobs` (sem JSON; payload em colunas tipadas): `id`, `kind` (`process_email` | `execute_action`), `priority` int, `status` (`queued`|`running`|`done`|`dead`), `attempts`, `max_attempts`, `run_after`, `idempotency_key` (unique), `email_id` FK nullable, `action_id` int nullable (FK adicionada na Task 5), `last_error`, `locked_at`, `locked_by`, `created_at`, `finished_at`; check: `process_email` ⇒ `email_id not null`.
- `types.ProcessEmailJob(kind: Literal["process_email"], email_id: str)`, `types.ExecuteActionJob(kind: Literal["execute_action"], action_id: int)`, `JobPayload = Annotated[ProcessEmailJob | ExecuteActionJob, Field(discriminator="kind")]`; `ClaimedJob(id: int, attempts: int, payload: JobPayload)`.
- Prioridades: `PRIORITY = {"execute_action": 20, "process_email": 30}` (chat entrará com 50, backfill com 0).
- `JobStore(sf)`: `enqueue(payload: JobPayload) -> bool` (False se a chave já existe; chave `"<kind>:<id>"`), `claim(worker_id: str) -> ClaimedJob | None` (Postgres: `FOR UPDATE SKIP LOCKED`; SQLite: mesma query sem o lock), `complete(job_id)`, `fail(job_id, error: str) -> Literal["retry", "dead"]` (backoff `min(60 * 2**attempts, 3600)` s), `requeue_stale(older_than: timedelta) -> int`, `counts() -> JobCounts`.
- `Handler` (`Protocol`): `def __call__(self, job: ClaimedJob) -> None`. `Worker(store: JobStore, handlers: Mapping[JobKind, Handler], *, on_dead: Callable[[ClaimedJob, str], None], poll_s: float, sleep, worker_id: str)`; `.run_once() -> bool` (processou algo?), `.run_forever()` (requeue_stale no início e a cada 10 min; grava heartbeat em `/tmp/worker-heartbeat`).
- `src/worker.py` monta Store, JobStore, gateway (1 por processo), handlers (preenchidos nas Tasks 6–7; nesta task, mapa vazio + log de kind desconhecido → `fail`).

- [ ] **Step 1: Failing tests**
  - `test_enqueue_is_idempotent`: duas vezes `ProcessEmailJob(email_id="a")` → `True`, `False`; 1 linha.
  - `test_claim_orders_by_priority_then_age` e `test_claim_skips_future_run_after`.
  - `test_fail_backs_off_then_dead`: `max_attempts=2` → `"retry"` com `run_after` ≈ agora+120s, depois `"dead"`; `on_dead` chamado uma vez pelo `Worker`.
  - `test_handler_exception_marks_retry_and_worker_continues`.
  - `test_requeue_stale_returns_running_jobs_to_queue` (`locked_at` 20 min atrás → `queued`).
  - `test_payload_roundtrip_is_typed`: claim devolve `ProcessEmailJob`, não dict.
- [ ] **Step 2: Rodar** `pytest tests/test_jobs.py -q` → Expected: FAIL (`ModuleNotFoundError: src.jobs`).
- [ ] **Step 3: Implementar.** Postgres-only no claim via `s.bind.dialect.name`.
- [ ] **Step 4: Rodar** testes + migrations + mypy → Expected: PASS. Rodar também `tests/test_migrations.py` em Postgres via WSL (`docker` do WSL) para o `SKIP LOCKED`.
- [ ] **Step 5: Commit** `feat(jobs): postgres job queue and worker loop`

---

### Task 3: Runtime do agente (contexto, estado, executor de tools, loop, trilha)

**Files:**
- Create: `src/agent/context.py`, `src/agent/state.py`, `src/agent/registry.py`, `src/agent/executor.py`, `src/agent/loop.py`, `src/agent/trail.py`, `alembic/versions/0006_agent_runs_steps.py`
- Modify: `src/db/models.py` (`AgentConfig`, `AgentRun`, `AgentStep`)
- Test: `tests/test_agent_runtime.py`

**Interfaces:**
- `context.AgentContext` (frozen, `arbitrary_types_allowed`): `run: RunScope(run_id: int, trigger: Literal["webhook", "chat"], config_version: int)`, `email_id: str | None`, `llm: LLMDeps(structured: StructuredGenerator, tools: ToolCaller)`, `mail: MailReader`, `labels: Labeler`, `history: SenderHistorySource`, `actions: ActionSink`, `policy: PolicyReader`, `kb: KbReader`. Os `Protocol`s ficam em `src/agent/ports.py` (definidos aqui; implementados nas Tasks 4–6). `AgentContext.call(step_id: int) -> CallContext` monta o contexto de chamada LLM (`agent_run_id`, `agent_step_id`, `email_ids`).
- `state.EmailFacts(email: EmailRow, body: str | None = None, classification: Classification | None = None, sender: SenderStats | None = None, labels: tuple[str, ...] = ())`; `state.AgentState(emails: Mapping[str, EmailFacts], proposals: tuple[ProposalRef, ...] = (), turns_used: int = 0)`; `ProposalRef(action_id: int, kind: ActionKind, email_id: str, outcome: Literal["suggested", "auto", "denied", "duplicate"])`.
- `state.StateUpdate` = união discriminada por `kind`: `BodyFetched(email_id, body)`, `Classified(email_id, classification)`, `SenderLoaded(email_id, stats)`, `LabelsApplied(email_id, labels)`, `ActionProposed(ref: ProposalRef)`. `state.reduce(state: AgentState, update: StateUpdate) -> AgentState` (puro, `model_copy`).
- `registry.AgentTool[C: BaseModel, S: BaseModel]` (frozen, `arbitrary_types_allowed`): `tool: BaseTool`, `context_of: Callable[[AgentContext], C]`, `state_of: Callable[[AgentState], S]`, `contexts: frozenset[Literal["webhook", "chat"]]`, `planned_only: bool = False` (não oferecida ao modelo no webhook). `Registry(tools: Sequence[AgentTool[...]])`: `.for_model(trigger) -> list[BaseTool]`, `.get(name) -> AgentTool | None`.
- Saída de tool: `@tool(response_format="content_and_artifact", parse_docstring=True)` devolvendo `tuple[str, StateUpdate | None]`; `content` é o que o modelo lê.
- `executor.ToolExecutor(registry, trail)`: `.execute(call: ToolCallRequest, ctx: AgentContext, state: AgentState, *, source: Literal["planned", "model"]) -> ToolExecution(message: ChatMessage, update: StateUpdate | None, ok: bool)`. Abre `agent_step` (kind `tool`), projeta fatias, monta `ToolRuntime(state=S, context=C, config={}, stream_writer=_noop, tool_call_id=call.id, store=None)`, chama `tool.invoke({"type": "tool_call", ...})`, valida o artifact com `TypeAdapter(StateUpdate | None)`, fecha o step (`ok`/`error`, `detail` = args + resultado truncado a 2000). Erros (tool ausente, não disponível no trigger, `ValidationError` de args, exceção da tool, chamada idêntica repetida no run) → `ToolMessage` `"erro: …"`, `ok=False`, sem exceção.
- `loop.AgentLoop(executor, trail, caller: ToolCaller)`: `.run(ctx, state, prompt: Prompt, *, max_turns: int) -> LoopResult(state: AgentState, summary: str, status: Literal["done", "truncated"])`. Cada turno: step `llm` aberto antes (id vai no `CallContext`), `generate_turn`, executa tool_calls em ordem aplicando `reduce`, anexa mensagens; para quando o turno não tem tool_calls.
- `trail.Trail(sf)`: `start_run(trigger, email_id, config_version) -> int`, `open_step(run_id, kind: Literal["llm", "tool", "rule", "note"], name: str, source) -> int`, `close_step(step_id, status: Literal["ok", "error"], detail: str)`, `finish_run(run_id, status: Literal["done", "truncated", "failed"], summary: str | None, error: str | None)`. Tokens do run somados de `llm_calls` por `agent_run_id` na leitura (não duplicar).
- Tabelas: `agent_configs(id, version unique, active, decide_prompt text, max_turns int, created_at)` (seed v1 vazio aqui; prompt real na Task 6), `agent_runs(id, trigger, email_id FK null, config_id FK, status, summary, error, started_at, finished_at)`, `agent_steps(id, run_id FK, position, kind, name, source, status, detail text, started_at, finished_at)`; FKs `llm_calls.agent_run_id/agent_step_id` → essas tabelas.

- [ ] **Step 1: Failing tests** (tools de teste tipadas no arquivo de teste, com fatias próprias)
  - `test_tool_receives_only_its_slices`: tool `peek(runtime: ToolRuntime[PeekCtx, PeekState])` onde `PeekCtx` tem só `email_id` e `PeekState` só `classification`; registry projeta; assert que a tool viu `PeekCtx(email_id="a")` e que `PeekCtx` não tem `llm`/`actions` (`set(PeekCtx.model_fields) == {"email_id"}`).
  - `test_runtime_hidden_from_model_schema`: `"runtime" not in tool.tool_call_schema.model_json_schema()["properties"]`.
  - `test_update_is_reduced_into_state`: tool devolve `Classified` → `state.emails["a"].classification` preenchido; estado original intacto.
  - `test_unknown_tool_denied_tool_bad_args_and_repeat_become_error_messages` (4 casos parametrizados) → `ok=False`, conteúdo começa com `"erro:"`, step `error`.
  - `test_loop_stops_on_plain_answer`: `FakeToolCaller([turn(tool_calls=[peek]), turn("arquivar não; nada a fazer")])` → `status="done"`, `summary` = texto, 2 steps llm + 1 tool, em ordem `position` 0..2.
  - `test_loop_truncates_at_max_turns`: caller sempre pede tool → `status="truncated"` após `max_turns` chamadas LLM.
  - `test_llm_step_id_travels_in_call_context`: fake caller captura `prompt.context.agent_step_id` == id do step llm aberto.
- [ ] **Step 2: Rodar** `pytest tests/test_agent_runtime.py -q` → Expected: FAIL (módulos ausentes).
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar** testes + migrations + mypy → Expected: PASS.
- [ ] **Step 5: Commit** `feat(agent): typed runtime, sliced ToolRuntime executor, loop and run trail`

---

### Task 4: Tools refatoradas sobre `ToolRuntime`

**Files:**
- Delete: `src/agent/tools.py` (legado sem tipos; `create_agent`, `AgentLogger`, prompts antigos saem)
- Create: `src/agent/tools/__init__.py` (`build_registry() -> Registry`), `src/agent/tools/mail.py`, `src/agent/tools/classify.py`, `src/agent/tools/sender.py`, `src/gmail/reader.py`, `src/agent/sender_history.py`
- Modify: `src/classification/classifier.py` (extrair `classify_one(...)` reutilizável), `src/db/store.py` (consultas de histórico do remetente)
- Test: `tests/test_agent_tools.py`, `tests/test_gmail_reader.py`

**Interfaces:**
- `ports.MailReader` (`Protocol`): `list_messages(query: MailQuery) -> MailPage`, `get_body(email_id: str) -> str`, `list_labels() -> tuple[LabelInfo, ...]`. `src/gmail/reader.py::GmailReader(service_factory: Callable[[], Resource])` implementa: `get_body` lê `format=full`, prefere `text/plain`, senão HTML → texto (stdlib `html.parser`, sem dependência nova), remove invisíveis, trunca a `BODY_MAX_CHARS = 6000`. Respostas do Gmail validadas por modelos `_` privados, como em `messages.py`.
- Tools e fatias (contexto `C` / estado `S`; nada além disso):
  | tool | `C` | `S` | update | triggers |
  |---|---|---|---|---|
  | `classify_email()` | `ClassifyCtx(llm: StructuredGenerator, taxonomy: TaxonomySource, call: CallContext)` | `ClassifyState(email: EmailRow, body: str \| None)` | `Classified` | webhook (planned), chat |
  | `get_email_body()` | `BodyCtx(mail: MailReader, email_id: str)` | `BodyState(body: str \| None)` (se já tem, devolve sem chamar Gmail) | `BodyFetched` | webhook, chat |
  | `sender_history()` | `SenderCtx(history: SenderHistorySource)` | `SenderState(sender: str \| None)` | `SenderLoaded` | webhook (planned), chat |
  | `list_emails(query, label_ids, max_results, page_token, include_spam_trash)` | `ListCtx(mail: MailReader)` | `NoState` | `None` | chat |
  | `list_labels()` | `ListCtx` | `NoState` | `None` | chat |
  - No webhook, a fatia de estado de `classify_email`/`get_email_body`/`sender_history` vem de `state.emails[ctx.email_id]`; tools sem argumento de e-mail operam sobre o e-mail do run (o modelo do webhook não escolhe alvo). No chat (subprojeto 5) a projeção muda, a tool não.
  - `classify_email` monta o prompt com `build_system_prompt(taxonomy, preferences)` e usa `body` do estado quando presente (snippet senão); chama `runtime.context.llm.generate_structured(prompt.with_context(runtime.context.call), BatchResult)`; reaproveita validação de categoria/deadline do `Classifier` (`classify_one`).
  - `SenderStats(address: str, total: int, by_category: tuple[CategoryCount, ...], actions: tuple[ActionOutcomeCount, ...], last_seen: datetime | None)` — `actions` conta sugestões por `(kind, status)` (vazio até a Task 5 existir; a consulta é escrita lá e injetada aqui via `SenderHistorySource`).
- `build_registry()` registra as cinco tools com suas projeções; `propose_action` e `apply_labels` entram nas Tasks 5–6.

- [ ] **Step 1: Failing tests**
  - Para cada tool: invocar via `ToolExecutor` com fakes de porta e assert do `StateUpdate` e do conteúdo; ex.: `test_get_email_body_uses_state_before_gmail` (estado com corpo → fake `MailReader` com 0 chamadas).
  - `test_classify_email_uses_runtime_call_context`: fake `StructuredGenerator` captura `prompt.context.agent_step_id` e `email_ids == ("a",)`.
  - `test_classify_email_invalid_category_is_error_not_update` → `ok=False`, sem `Classified`.
  - `test_tool_slices_are_minimal`: para cada `AgentTool` do registry, `set(C.model_fields)` igual ao esperado da tabela (trava regressão de isolamento).
  - `test_registry_offers_chat_only_tools_only_in_chat`: `for_model("webhook")` = `{get_email_body}` nesta task.
  - `test_reader_html_body_to_text` e `test_reader_prefers_text_plain` (FakeService).
- [ ] **Step 2: Rodar** → Expected: FAIL.
- [ ] **Step 3: Implementar.** `grep -rn "src.agent.tools" src tests` deve ficar vazio fora do novo pacote.
- [ ] **Step 4: Rodar** testes + suíte inteira + mypy → Expected: PASS.
- [ ] **Step 5: Commit** `refactor(agent): typed ToolRuntime tools with sliced context; drop legacy tools`

---

### Task 5: Política, ações sugeridas e regras de automação

**Files:**
- Create: `src/agent/policy.py`, `src/agent/actions.py`, `src/agent/rules.py`, `src/agent/tools/act.py`, `alembic/versions/0007_actions_rules_policies.py`
- Modify: `src/db/models.py`, `src/db/store.py` (ou `src/agent/store.py` para as consultas do agente, se `store.py` passar de ~500 linhas), `src/agent/tools/__init__.py`, `src/agent/sender_history.py`
- Test: `tests/test_policy_actions.py`, `tests/test_rules.py`

**Interfaces:**
- `ActionKind = Literal["mark_read", "star", "archive", "trash", "block_sender"]`; `NEVER_AUTO: frozenset[ActionKind] = frozenset({"block_sender"})`.
- `tool_policies(id, target, trigger, policy)` unique `(target, trigger)`; `target` ∈ `ActionKind` ∪ `{"apply_labels"}`; `policy` ∈ `auto|suggest|deny`. Seed: `apply_labels`→`auto` (webhook, chat); todo `ActionKind`→`suggest` (webhook, chat).
- `policy.PolicyResolver(store)`: `.resolve(target, trigger, *, rule_mode: Literal["suggest", "auto"] | None = None) -> Literal["auto", "suggest", "deny"]`. Ordem: `deny` do banco vence; regra `auto` só vale se o banco não negar; qualquer `auto` em `NEVER_AUTO` vira `suggest`; ausência de linha = `suggest`.
- `suggested_actions(id, email_id FK, kind, reason, status pending|approved|rejected|executed|failed, origin agent|rule, run_id FK, rule_id FK null, batch_id null, error, created_at, decided_at, executed_at)`; índice único parcial `(email_id, kind) WHERE status IN ('pending','approved')`. FK `jobs.action_id` adicionada aqui.
- `actions.ActionService(store, jobs: JobStore, policy)` implementa `ports.ActionSink`: `.propose(email_id, kind, reason, *, run_id, trigger, rule: RuleRef | None) -> ProposalRef` — `deny`→`denied` sem linha; duplicata→`duplicate`; `suggest`→linha `pending`; `auto`→linha `approved` + `enqueue(ExecuteActionJob)`.
- `automation_rules(id, name, sender_address null, sender_domain null, category null, subject_contains null, action, mode, active, fired_count, approved_count, rejected_count, source, created_at)`; check: pelo menos uma condição não nula. `rules.RuleEngine(store)`: `.match(email: EmailRow, classification: Classification) -> tuple[RuleMatch, ...]` (AND das condições não nulas; comparação case-insensitive; domínio extraído do `sender` "Nome <a@b.c>").
- Tool `propose_action(kind: ActionKind, reason: str)`: `C = ActCtx(actions: ActionSink, email_id: str, run: RunScope)`, `S = ActState(proposals: tuple[ProposalRef, ...])`; update `ActionProposed`; conteúdo diz ao modelo o desfecho (`"sugerido #12 (aguarda aprovação)"`, `"negado pela política"`, `"já sugerido"`). Disponível webhook + chat.
- `SenderHistorySource` passa a contar `suggested_actions` do remetente por `(kind, status)` — é o sinal de aprendizado mais forte ("você recusou arquivar este remetente 3×").

- [ ] **Step 1: Failing tests**
  - `test_block_sender_never_auto` parametrizado: banco `auto`, regra `auto`, ambos → `suggest`.
  - `test_db_deny_beats_rule_auto`; `test_missing_policy_defaults_to_suggest`.
  - `test_auto_action_is_approved_and_enqueued` (job `execute_action` criado).
  - `test_duplicate_pending_proposal_is_not_created` (Review Focus 1).
  - `test_rule_matches_domain_and_category_case_insensitive`; `test_rule_without_conditions_rejected_by_db`.
  - `test_propose_action_tool_reports_outcome_to_model` via `ToolExecutor`.
  - `test_sender_history_counts_past_outcomes`.
- [ ] **Step 2: Rodar** → Expected: FAIL.
- [ ] **Step 3: Implementar** (+ seed na migração).
- [ ] **Step 4: Rodar** testes + migrations (SQLite e Postgres) + mypy → Expected: PASS.
- [ ] **Step 5: Commit** `feat(agent): action policy, suggested actions and automation rules`

---

### Task 6: Fluxo `process_email` + labels + webhook enfileirando

**Files:**
- Create: `src/agent/flows/process_email.py`, `src/agent/prompts.py`, `src/gmail/labels.py`, `src/agent/tools/labels.py`, `alembic/versions/0008_agent_config_v1_labels.py`
- Modify: `src/services/sync.py` (enfileira em vez de classificar), `src/api/app.py` (não monta mais classificador/gateway), `src/worker.py` (handler), `src/db/models.py` (`GmailLabel(name unique, gmail_id)`)
- Test: `tests/test_process_email.py`, `tests/test_sync.py`, `tests/test_gmail_labels.py`, `tests/test_api.py`

**Interfaces:**
- `ProcessEmailFlow(trail, executor, loop, rules: RuleEngine, actions: ActionSink, config: AgentConfigSource, context_factory: Callable[[int, str], AgentContext])`: `.handle(job: ClaimedJob) -> None` — segue o "Modelo do fluxo" passos 1–6. Planejados chamam `executor.execute(ToolCallRequest(id="planned-<n>", name=..., args={}), ..., source="planned")`. Se `classify_email` falhar → run `failed` e exceção (job faz retry); falha em labels/histórico → step `error`, fluxo segue. E-mail já com run `done` para a mesma `config_version` → nota e retorna (idempotente em retry pós-sucesso).
- Regra casada → step `rule` + `propose(..., rule=...)` por regra, `fired_count += 1`, sem loop LLM. Sem regra → `loop.run` com `decide_prompt` da config ativa.
- `prompts.build_decide_prompt(config, facts: EmailFacts, kb: Sequence[str]) -> Prompt`: system = `config.decide_prompt` + bloco "Preferências e feedback" (KB) + regra anti-injeção; human = `<email_data>` com remetente/assunto/data/snippet/classificação/histórico do remetente serializados, explicitamente marcados como dado. Seed v1 do `decide_prompt` (migração 0008) instrui: objetivo é desentulhar sem perder nada importante; na dúvida não sugerir; nunca sugerir lixeira/arquivar para `is_relevant` ou com prazo; usar histórico de recusas do remetente; uma sugestão por tipo; terminar com uma frase de resumo em pt-BR.
- `labels.GmailLabeler(service_factory, store)` implementa `ports.Labeler`: `.apply(email_id, names: Sequence[str]) -> tuple[str, ...]` cria labels faltantes (`labels.create`, cache em `gmail_labels`) e faz `messages.modify(addLabelIds=…)`. Nomes: `IA/<categoria>` (`/`→`-` dentro do nome da categoria, ex. `IA/Infra-Dev`) e `IA/Relevante` quando `is_relevant`.
- Tool `apply_labels()` (`planned_only`): `C = LabelCtx(labels: Labeler, email_id: str, policy: Literal["auto", "suggest", "deny"])`, `S = LabelState(classification: Classification | None)`; sem classificação → erro; `policy != "auto"` → não aplica e diz por quê.
- `sync_inbox(store, service, jobs: JobStore) -> SyncResult`: depois do ingest, `enqueue(ProcessEmailJob)` para cada novo id; `SyncResult(ingested, enqueued)`. CLI `python -m src.cli.enqueue --unprocessed` enfileira e-mails sem run `done` (para o deploy inicial).
- Handler registrado em `src/worker.py`: `{"process_email": flow.handle}`; gmail service por job (não thread-safe).

- [ ] **Step 1: Failing tests** (fakes de portas + `FakeToolCaller` + `FakeStructured`)
  - `test_new_email_happy_path_trail`: steps em ordem `classify_email`, `apply_labels`, `sender_history`, `llm`, `propose_action`, `llm`; run `done`; sugestão `archive` pendente; `email.category` gravada; labels `("IA/Promoções",)`.
  - `test_rule_match_skips_decide_loop`: regra `sender_domain` → 0 chamadas `generate_turn`, sugestão com `origin="rule"`, `fired_count == 1`.
  - `test_classification_failure_fails_run_and_raises` → job retry; nenhum label aplicado.
  - `test_label_failure_does_not_stop_flow`.
  - `test_reprocessing_done_email_is_noop`.
  - `test_decide_prompt_marks_email_as_data`: conteúdo humano dentro de `<email_data>`; system contém texto da KB `scope=decision`.
  - `test_sync_enqueues_new_emails_instead_of_classifying` (atualiza `test_sync.py`; nenhum `classify_pending` no caminho do webhook).
  - `test_labeler_creates_missing_label_once` (FakeService; 2 e-mails → 1 `labels.create`).
- [ ] **Step 2: Rodar** → Expected: FAIL.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar** suíte inteira + mypy → Expected: PASS.
- [ ] **Step 5: Commit** `feat(agent): process_email flow, IA labels and webhook enqueue`

---

### Task 7: Execução, aprovação via CLI e feedback → KB

**Files:**
- Create: `src/gmail/actions.py`, `src/agent/flows/execute_action.py`, `src/cli/actions.py`, `src/cli/rules.py`, `alembic/versions/0009_feedback.py`
- Modify: `src/gmail/client.py` (`get_gmail_service(extra_scopes: Sequence[str] = ())` + `has_scope(scope) -> bool`), `src/gmail/authorize.py` (pede `gmail.settings.basic` também), `src/db/models.py` (`Feedback`; `kb_entries.feedback_id` nullable), `src/worker.py`
- Test: `tests/test_execute_action.py`, `tests/test_cli_actions.py`, `tests/test_gmail_actions.py`

**Interfaces:**
- `GmailActions(service_factory, has_scope)`: `.execute(kind: ActionKind, email: EmailRow) -> None` — `mark_read` remove `UNREAD`; `star` adiciona `STARRED`; `archive` remove `INBOX`; `trash` `messages.trash`; `block_sender` cria filtro `criteria.from=<endereço>` com `addLabelIds=["TRASH"], removeLabelIds=["INBOX"]` e levanta `MissingScope("reautorize com python -m src.gmail.authorize")` sem o escopo. Não existe método que chame `messages.delete` (teste varre o módulo).
- `ExecuteActionFlow(store, gmail: GmailActions, trail)`: `.handle(job)` — só executa se `status == "approved"`; sucesso → `executed` (+ `approved_count` da regra); `MissingScope` → `failed` sem retry; outros erros → exceção (retry pela fila), `failed` quando `dead`.
- `feedback(id, action_id FK null, email_id FK null, verdict approve|reject|comment, text null, source cli|ui|chat|audio, kb_entry_id FK null, created_at)`.
- `src.cli.actions`: `list [--sender S] [--kind K]` (tabela: id, remetente, assunto, ação, motivo, origem), `approve (--id N... | --sender S | --rule R | --kind K) [--yes]`, `reject (… mesmos seletores) [--comment TEXT]`. Lote mostra contagem e pede confirmação salvo `--yes`. Aprovar → `approved` + jobs; recusar → `rejected` + `feedback`; com `--comment` → `kb_entries(kind="feedback", scope="decision", source="approval", text="Recusado: <ação> em e-mails de <remetente> (motivo do agente: …). Comentário: …")`.
- `src.cli.rules`: `add --name N [--sender A] [--domain D] [--category C] [--subject S] --action K` (sempre `suggest`), `list` (com contadores), `promote --id N` (recusa se `action in NEVER_AUTO`; mostra disparos/aprovadas/recusadas e pede confirmação), `disable --id N`.

- [ ] **Step 1: Failing tests**
  - `test_execute_only_approved` (pendente → no-op + nota).
  - `test_each_kind_maps_to_gmail_call` parametrizado (FakeService registra `messages.modify` / `messages.trash` / `settings.filters.create`).
  - `test_block_sender_without_scope_fails_without_retry`.
  - `test_no_permanent_delete_anywhere`: `"messages().delete" not in` fonte de `src/gmail/actions.py` e nenhuma chamada `.delete(` no FakeService ao executar todos os kinds.
  - `test_cli_reject_with_comment_creates_feedback_and_kb` e `test_cli_approve_by_sender_batches` (CLI chamada como função `main(argv)`).
  - `test_promote_refuses_block_sender`.
- [ ] **Step 2: Rodar** → Expected: FAIL.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar** suíte + mypy → Expected: PASS.
- [ ] **Step 5: Commit** `feat(agent): approved action execution, approval CLI and feedback into KB`

---

### Task 8: Alertas por e-mail

**Files:**
- Create: `src/alerts/__init__.py`, `src/alerts/service.py`, `alembic/versions/0010_notifications.py`
- Modify: `src/gmail/messages.py` (lê header `X-Gmail-Integration`; `MessageSummary.system: bool`), `src/services/sync.py` (ignora `system`), `src/worker.py` (`on_dead`, falhas em série), `src/api/app.py` (falha repetida de sync/watch), `src/db/models.py` (`Notification`)
- Test: `tests/test_alerts.py`, `tests/test_sync.py`, `tests/test_messages.py`

**Interfaces:**
- `AlertKind = Literal["job_dead", "agent_failures", "sync_failures", "watch_failures"]`.
- `notifications(id, kind, subject, body, status sent|suppressed|failed, suppressed_count, error, created_at, sent_at)`.
- `AlertService(store, send: MailSender, clock)`: `.alert(kind, subject, body) -> Literal["sent", "suppressed", "failed"]` — se houve `sent` do mesmo `kind` < 15 min → `suppressed`; no próximo envio o corpo inclui "+N alertas suprimidos". Nunca levanta (falha de envio vira `failed` + log).
- `MailSender` (`Protocol`): `.send_to_self(subject: str, body: str) -> None`. `GmailMailSender` monta MIME com `Subject: [gmail-integration] <subject>`, header `X-Gmail-Integration: alert`, envia com `messages.send` ao `watch_state.email_address` e aplica label `Sistema/Alerta` (via `GmailLabeler`). Envio de sistema não passa por agente nem fila.
- Gatilhos: `Worker.on_dead` → `job_dead` (kind, id, erro); 3 runs `failed` seguidos no worker → `agent_failures`; 3 falhas seguidas de `sync_inbox` → `sync_failures`; falha de renovação do watch → `watch_failures`.

- [ ] **Step 1: Failing tests**
  - `test_second_alert_within_15min_is_suppressed_then_counted`.
  - `test_send_failure_never_raises`.
  - `test_alert_mail_has_header_prefix_and_label`.
  - `test_sync_ignores_system_alert_messages` (Review Focus 5): history traz id cujo metadata tem o header → não ingerido, nenhum job.
  - `test_worker_dead_job_triggers_alert` e `test_three_failed_runs_trigger_agent_failures_alert`.
- [ ] **Step 2: Rodar** → Expected: FAIL.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar** suíte + mypy → Expected: PASS.
- [ ] **Step 5: Commit** `feat(alerts): rate-limited email alerts from the server to itself`

---

### Task 9: Deploy do worker + verificação real + docs

**Files:**
- Modify: `docker-compose.yml` (serviço `worker`: `<<: *app`, `command: ["python", "-m", "src.worker"]`, mesmo `user`/volume de `secrets`, depende de `migrate`; healthcheck: heartbeat com menos de 120 s), `deploy/deploy.sh` (smoke exige `api` e `worker` `healthy`), `tests/test_deploy_script.py`, `README.md` (seção "Agente": fluxo, CLI de aprovação/regras, reautorização para `block_sender`), `.env.example` se existir
- Test: `tests/test_deploy_script.py`

- [ ] **Step 1: Failing test** `test_smoke_requires_worker_healthy` (worker `unhealthy` → rollback).
- [ ] **Step 2: Rodar** → Expected: FAIL.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Verificação real (Postgres descartável no WSL + Gemini real, sem tocar produção):** `alembic upgrade head`; copiar 10 e-mails já classificados; `python -m src.cli.enqueue --unprocessed`; rodar `python -m src.worker` até a fila esvaziar com o Gmail em modo somente-leitura (labels via fake: `LABELS_DRY_RUN=1` faz o `GmailLabeler` só logar). Expected: 10 runs `done`, trilhas legíveis (`agent_steps` em ordem), sugestões plausíveis, nenhuma para e-mail relevante; `llm_calls.agent_run_id` preenchido.
- [ ] **Step 5: Rodar** suíte inteira + mypy + alembic up/down/up em Postgres → Expected: PASS.
- [ ] **Step 6: Commit** `feat(deploy): run the agent worker in compose with health-gated deploy`; PR `feat/agent-core` → `main` (merge pelo João; o deploy automático sobe o worker).
