# Multi-tenant — Fase 0 (spike OAuth) + Fase 1 (base multi-conta) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validar que o app OAuth não verificado serve para amigos (fase 0) e tornar o banco e o runtime multi-conta, com a conta do João como nº 1 e comportamento visível idêntico ao de hoje (fase 1).

**Architecture:** Todo dado de conteúdo ganha `gmail_account_id`. Um módulo `src/db/scope.py` guarda a conta corrente num `ContextVar`; eventos do SQLAlchemy aplicam o filtro por conta em todo SELECT/UPDATE/DELETE ORM, preenchem a conta em todo INSERT e, no Postgres, fazem `SET LOCAL app.account_id` para as políticas de RLS (`FORCE ROW LEVEL SECURITY`). Sem escopo, acessar tabela de conteúdo levanta `ScopeError` (falha fechada). Os pontos de entrada (request da API, job do worker, sync do webhook, CLI, alertas) abrem o escopo.

**Tech Stack:** Python 3.12, SQLAlchemy 2.1, Alembic, Postgres 16 + pgvector, FastAPI, pytest (SQLite em memória + Postgres via `TEST_DATABASE_URL`), mypy strict.

**Spec:** `docs/superpowers/specs/2026-11-23-multi-tenant-design.md`

## Global Constraints

- Tudo tipado em Pydantic; `Any` proibido (mypy strict + `disallow_any_explicit`).
- **Sem colunas JSON/JSONB.**
- Isolamento: "Cada usuário vê **só os próprios dados**. Nenhuma tela ou API mostra dado de outro — nem para o João como admin."
- Fase 1: "Comportamento visível igual ao de hoje."
- Migração do João: "contagens antes/depois iguais em todas as tabelas", sem reprocessar nada.
- Caminhos de sistema (sem filtro de conta) são **listados**: claim/housekeeping de jobs, roteamento do webhook, renovação de watch, busca da conta do admin, métricas, exclusão. Nenhum devolve conteúdo.
- Política RLS: `gmail_account_id = NULLIF(current_setting('app.account_id', true), '')::int OR current_setting('app.system', true) = 'on'`.
- CI roda `alembic upgrade head && alembic downgrade base && alembic upgrade head` — a migração nova precisa de downgrade.
- Execução inline, sem subagentes (preferência do João).

## Decisões deste plano (desvios conscientes da spec)

- Só as tabelas que a fase 1 usa: `gmail_accounts` (sem colunas de token — fase 2), `account_settings`, `users.email` + `users.role`. `invites`, `oauth_clients`, `user_llm_keys` e o resto de `users` entram nas fases que os usam.
- O `token.json` continua sendo a credencial do Gmail nesta fase (fase 2 move para o banco).
- `watch_state` é absorvida por `gmail_accounts` já aqui; a API de `Store` (`get_watch_state`/`init_watch_state`) mantém os nomes.
- Isolamento sem papel novo no Postgres: `FORCE ROW LEVEL SECURITY` faz a política valer também para o dono das tabelas (o usuário atual da app).

## Review Focus

1. Código rodando fora de qualquer escopo (thread nova, `BackgroundTasks`, hook de startup, laço de renovação) → deve falhar com `ScopeError`, nunca ler tudo nem nada em silêncio. Teste na Task 1 (`test_thread_without_scope_fails_closed`).
2. Id de outra conta adivinhado numa rota de detalhe → 404, nunca o dado nem 403 revelando existência. Teste na Task 8.
3. O mesmo id de mensagem do Gmail em duas contas → guardado nas duas, independente. Teste na Task 3 (`test_same_gmail_id_in_two_accounts`).
4. Dados de produção com referências órfãs (ex.: `chat_message_refs`/`chat_card_items`/`llm_call_emails` apontando para e-mail inexistente) → a migração não pode quebrar no meio. Pré-checagem na Task 10 e teste na Task 9 (`test_migration_with_orphan_refs`).
5. Downgrade com mais de uma conta → recusa com mensagem clara, sem apagar dados. Teste na Task 9 (`test_downgrade_refuses_with_two_accounts`).

---

## Fase 0

### Task 0: Spike do OAuth não verificado (manual + script descartável)

**Files:**
- Create (scratchpad, fora do repo): `spike_oauth.py`

**Interfaces:** nenhuma; o resultado é uma decisão registrada na memória do projeto e no corpo do PR da fase 1.

- [ ] **Step 1: João, no console do GCP (projeto atual)** — Google Auth Platform → Clients → criar **Web application** "Inbox Agent Web" com redirects `http://localhost:8765/callback` e `https://jsouza181.duckdns.org/api/oauth/google/callback`; Branding: nome, e-mail de suporte, domínio autorizado `jsouza181.duckdns.org` (anotar se o console aceita), link de política de privacidade (provisório: `https://jsouza181.duckdns.org/privacidade`); Audience: **Publish app → In production**. Baixar o JSON do client para o scratchpad.
- [ ] **Step 2: Escrever `spike_oauth.py`** — `google_auth_oauthlib.flow.Flow.from_client_secrets_file(path, scopes=["openid", "https://www.googleapis.com/auth/userinfo.email", "https://www.googleapis.com/auth/gmail.modify", "https://www.googleapis.com/auth/gmail.settings.basic"], redirect_uri="http://localhost:8765/callback")`, `authorization_url(access_type="offline", prompt="consent")`, servidor local de uma rota que troca o `code`, imprime `refresh_token is not None`, e-mail da conta e `expires_in`; salva o token num arquivo do scratchpad.
- [ ] **Step 3: Rodar com uma conta Google que NÃO é a do projeto** (secundária do João ou de um amigo). Registrar: aparece "O Google não verificou este app"? Dá para continuar por "Avançado"? Veio `refresh_token`? Algum "This app is blocked"?
- [ ] **Step 4: Dia 8 (≥ 7 dias depois)** — rodar `Credentials.from_authorized_user_file(...).refresh(Request())` com o token salvo. Esperado: refresh funciona (prova que não há a expiração de 7 dias do modo Testing).
- [ ] **Step 5: Registrar o resultado** em `project-roadmap.md` (memória). Se o Step 3 bloquear ou o Step 4 falhar: **parar e voltar ao João** antes da fase 2 (a fase 1 não depende disso e pode seguir).

---

## Fase 1

### Task 1: Escopo de conta (`src/db/scope.py`)

**Files:**
- Create: `src/db/scope.py`
- Modify: `src/db/session.py` (chamar `install_scoping` em `make_session_factory`)
- Modify: `src/db/models.py` (adiciona `GmailAccount` — definição na Task 2, Interfaces — porque o FK do mixin aponta para ela)
- Test: `tests/test_scope.py` (tabela de teste `_Note(AccountOwned, Base)`)

**Interfaces:**
- Produces:
  - `class ScopeError(RuntimeError)`
  - `account_scope(account_id: int) -> contextlib.AbstractContextManager[None]`
  - `system_scope() -> contextlib.AbstractContextManager[None]`
  - `current_account() -> int` (levanta `ScopeError` fora de `account_scope`)
  - `in_system_scope() -> bool`
  - `class AccountOwned` (mixin declarativo em `src/db/scope.py`, importado por `models.py`): `gmail_account_id: Mapped[int]` com `ForeignKey("gmail_accounts.id")`, `nullable=False`, `index=True`, declarado via `@declared_attr` para que `Email` possa sobrescrever como parte da PK.
  - `install_scoping(sf: sessionmaker[Session]) -> None`

Comportamento de `install_scoping` (eventos no `sessionmaker`):
- `do_orm_execute`: se algum mapper do statement (`state.all_mappers`) é subclasse de `AccountOwned` — em `account_scope` adiciona `with_loader_criteria(AccountOwned, lambda c: c.gmail_account_id == <id>, include_aliases=True)`; em `system_scope` não filtra; sem escopo levanta `ScopeError`.
- `before_flush`: instância nova de `AccountOwned` com `gmail_account_id is None` recebe `current_account()`.
- `after_begin` (só Postgres): `SET LOCAL app.account_id = '<id>'` ou `SET LOCAL app.system = 'on'`; sem escopo não define nada (RLS devolve zero linhas).

- [ ] **Step 1: Write the failing tests** (`tests/test_scope.py`, com a tabela de teste `_Note(AccountOwned, Base)` e duas linhas de `GmailAccount`)

```python
def test_reads_only_current_account(sf): ...       # A grava 1, B grava 1; em account_scope(A) select devolve só a de A
def test_insert_fills_account(sf): ...             # s.add(_Note(text="x")) em account_scope(7) -> gmail_account_id == 7
def test_get_by_pk_respects_scope(sf): ...         # s.get(_Note, id_de_B) em account_scope(A) is None
def test_update_delete_respect_scope(sf): ...      # update(_Note).values(text="y") em A não altera a de B; idem delete
def test_no_scope_raises(sf): ...                  # pytest.raises(ScopeError) ao selecionar _Note sem escopo
def test_system_scope_sees_all(sf): ...            # system_scope() -> 2 linhas
def test_thread_without_scope_fails_closed(sf): ...  # thread nova dentro de account_scope(A) não herda; select levanta ScopeError
def test_non_owned_tables_need_no_scope(sf): ...   # select em tabela sem AccountOwned funciona sem escopo
```

- [ ] **Step 2: Run** `python -m pytest tests/test_scope.py -v` — Expected: FAIL (`ModuleNotFoundError: src.db.scope`).
- [ ] **Step 3: Implement `src/db/scope.py`** e chamar `install_scoping(sf)` dentro de `make_session_factory` antes do `return`. Se `Session.get` não passar por `do_orm_execute` em algum caso, o teste `test_get_by_pk_respects_scope` mostra — aí também tratar `refresh`/identity map (sessões são curtas; não reutilizar sessão entre escopos).
- [ ] **Step 4: Run** `python -m pytest tests/test_scope.py -v && python -m mypy` — Expected: PASS, mypy sem erros.
- [ ] **Step 5: Commit** `feat(db): account scope with fail-closed ORM criteria`

### Task 2: Modelos — `gmail_accounts`, `account_settings`, `gmail_account_id` em todo conteúdo

**Files:**
- Modify: `src/db/models.py`
- Modify: `tests/conftest.py`
- Modify: `src/db/store.py:33-43` (`_insert_ignore`), `src/jobs/store.py:100-111` (`enqueue`) — inserts Core não passam pelo `before_flush`
- Test: `tests/test_scope.py` (acrescenta), suíte inteira

**Interfaces:**
- Consumes: `AccountOwned`, `current_account()` (Task 1)
- Produces:
  - `class GmailAccount(Base)` (criada já na Task 1), tabela `gmail_accounts`: `id` int PK, `user_id` FK `users.id` **unique**, `email_address` text unique (minúsculo), `status` str(16) (`connected` | `disconnected` | `revoked`), `history_id` str(32) nulo, `watch_expiration` bigint nulo, `connected_at` timestamptz, `updated_at` timestamptz.
  - `class AccountSettings(AccountOwned, Base)`, tabela `account_settings`: PK `gmail_account_id`, `llm_mode` str(8) nulo, `backfill_paused` bool default false, `backfill_turbo` bool default false, `updated_at`.
  - `User` + `email` text unique nulo (preenchido na migração), `role` str(16) default `member`.
  - `AccountOwned` aplicado a: `runs`, `run_emails`, `emails`, `email_embeddings`, `attachments`, `category_sets`, `category_flags`, `categories`, `category_examples`, `kb_entries`, `agent_configs`, `agent_runs`, `agent_steps`, `gmail_labels`, `tool_policies`, `automation_rules`, `suggested_actions`, `feedback`, `notifications`, `jobs`, `llm_calls`, `llm_call_messages`, `llm_call_emails`, `config_audit`, `chat_conversations`, `chat_messages`, `chat_message_refs`, `chat_audio`, `chat_cards`, `chat_card_items`.
  - Unicidades por conta: `category_sets(gmail_account_id, version)`, `agent_configs(gmail_account_id, version)`, `tool_policies(gmail_account_id, target, trigger)`, `gmail_labels` PK `(gmail_account_id, name)`, `jobs(gmail_account_id, idempotency_key)`, `uq_suggested_actions_live` com `gmail_account_id` na frente.
  - Fixtures em `tests/conftest.py`: `session_factory` cria tabelas e, em `system_scope`, `User(id=1)` + `GmailAccount(id=1, user_id=1, email_address="me@example.com", status="connected")`; fixture **autouse** `_account_scope` que roda o teste dentro de `account_scope(1)`, desligada pelo marker `@pytest.mark.noscope` (registrar em `pytest.ini`); fixture `second_account` que cria `User(id=2)` + `GmailAccount(id=2, email_address="other@example.com")` e devolve `2`.

- [ ] **Step 1: Write the failing test** `test_models_owned_tables` em `tests/test_scope.py`: para cada tabela da lista acima, `"gmail_account_id" in Base.metadata.tables[name].c`; e `test_unique_version_per_account`: duas contas podem ter `CategorySet(version=1)` cada.
- [ ] **Step 2: Run** `python -m pytest tests/test_scope.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** os modelos; `_insert_ignore` e `JobStore.enqueue` acrescentam `gmail_account_id=current_account()` a cada linha. `WatchState` continua no modelo até a Task 4.
- [ ] **Step 4: Run** `python -m pytest -q && python -m mypy` — Expected: suíte inteira PASS (testes que constroem linhas de conteúdo funcionam via autouse + `before_flush`; ajustar os que usam inserts Core ou `s.get(GmailLabel, name)`).
- [ ] **Step 5: Commit** `feat(db): gmail_accounts, account_settings and per-account ownership`

### Task 3: Chave composta de `emails`

**Files:**
- Modify: `src/db/models.py` (PK de `Email` = `(gmail_account_id, id)`; `email_embeddings` PK `(gmail_account_id, email_id)`; FKs para `emails` viram `ForeignKeyConstraint(["gmail_account_id", "email_id"], ["emails.gmail_account_id", "emails.id"])` em `run_emails`, `agent_runs`, `suggested_actions`, `jobs`, `email_embeddings`, `attachments` e demais com `ForeignKey("emails.id")`)
- Modify: `src/db/scope.py` (helper)
- Modify (call sites de `s.get(Email…)`/`s.get(EmailEmbedding…)`): `src/api/queries.py:233`, `src/api/routes/mail.py:83`, `src/chat/retrieval.py:91,96,108`, `src/db/store.py:180`, `src/indexing/store.py:104,141,171,186`
- Test: `tests/test_scope.py`

**Interfaces:**
- Produces: `email_key(email_id: str) -> tuple[int, str]` em `src/db/scope.py` = `(current_account(), email_id)`. Uso: `s.get(Email, email_key(email_id))`.

- [ ] **Step 1: Write the failing test** `test_same_gmail_id_in_two_accounts(session_factory, second_account)`: grava `Email(id="m1", subject="A")` em `account_scope(1)` e `Email(id="m1", subject="B")` em `account_scope(2)`; cada escopo lê o próprio `subject` via `Store` (`get_email` ou o método de leitura existente em `src/db/store.py:180`).
- [ ] **Step 2: Run** `python -m pytest tests/test_scope.py::test_same_gmail_id_in_two_accounts -v` — Expected: FAIL (`IntegrityError` de PK).
- [ ] **Step 3: Implement** PK/FKs compostas, `email_key` e troca dos 10 call sites; `grep -rn "get(Email\|get(EmailEmbedding" src` deve ficar só com `email_key(...)`.
- [ ] **Step 4: Run** `python -m pytest -q && python -m mypy` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(db): emails keyed by (account, gmail id)`

### Task 4: `watch_state` → `gmail_accounts`; settings por conta; consultas de sistema

**Files:**
- Modify: `src/db/store.py:291-320` (`get_watch_state`, `init_watch_state`), `src/db/rows.py` (`WatchStateRow`)
- Modify: `src/agent/settings.py` (`llm_mode`, `backfill_paused`, `backfill_turbo` leem/gravam `AccountSettings`; `mode_source`/`turbo_source` idem)
- Modify: `src/db/models.py` (remove `WatchState`)
- Create: `src/db/accounts.py`
- Test: `tests/test_accounts.py`, testes existentes de settings/watch

**Interfaces:**
- Consumes: `GmailAccount`, `AccountSettings`, `system_scope`, `current_account`
- Produces (`src/db/accounts.py`, todos abrem `system_scope` internamente e devolvem só ids/e-mails/status):
  - `connected_account_ids(sf: sessionmaker[Session]) -> list[int]`
  - `admin_account_id(sf: sessionmaker[Session]) -> int | None` (conta do usuário `role='admin'` de menor id)
  - `account_for_user(sf: sessionmaker[Session], user_id: int) -> int | None`
  - `account_by_email(sf: sessionmaker[Session], email_address: str) -> int | None` (minúsculo)
  - `Store.get_watch_state()` / `Store.init_watch_state(...)`: mesma assinatura, agora sobre `GmailAccount` da conta corrente (o `history_id` só é gravado quando ainda é nulo — mesma regra de hoje).
  - `RuntimeSettings`: mesma API pública; linha de `AccountSettings` criada sob demanda.

- [ ] **Step 1: Write the failing tests** (`tests/test_accounts.py`): `test_watch_state_per_account` (init em A não muda B; renovação não troca `history_id`), `test_settings_per_account` (`set_backfill_paused(True)` em A, B continua `False`), `test_admin_account_id`, `test_account_by_email_is_case_insensitive`, `test_connected_ids_skip_disconnected`.
- [ ] **Step 2: Run** `python -m pytest tests/test_accounts.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement.** `app_settings` fica só com chaves de plataforma (`global_lock`).
- [ ] **Step 4: Run** `python -m pytest -q && python -m mypy` — Expected: PASS.
- [ ] **Step 5: Commit** `feat: watch state and runtime settings per account`

### Task 5: Worker e fila por conta

**Files:**
- Modify: `src/jobs/types.py:93` (`ClaimedJob` + `account_id: int`)
- Modify: `src/jobs/store.py` (`claim`, housekeeping, contagens do painel)
- Modify: `src/jobs/worker.py:69-79` (`run_once`)
- Test: `tests/test_jobs.py`

**Interfaces:**
- Consumes: `system_scope`, `account_scope`
- Produces: `JobStore.claim(...)` roda em `system_scope` e devolve `ClaimedJob.account_id`; `Worker.run_once` executa `handler(job)` e o fechamento (done/fail/retry) dentro de `account_scope(job.account_id)`; requeue de jobs travados roda em `system_scope`. `JobStore.counts()` (usado pelo painel) passa a contar só a conta corrente.

- [ ] **Step 1: Write the failing tests** `test_handler_runs_in_job_account` (handler registra `current_account()`; job enfileirado em B → handler vê B) e `test_claim_sees_all_accounts` (`@pytest.mark.noscope`; jobs de A e B, dois `run_once` processam os dois).
- [ ] **Step 2: Run** `python -m pytest tests/test_jobs.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `python -m pytest -q && python -m mypy` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(jobs): claim across accounts, run each job in its account`

### Task 6: Pontos de entrada da API, sync, watch e alertas

**Files:**
- Create: `src/api/scope.py`
- Modify: `src/api/app.py` (registrar middleware; `run_sync`, `start_watch`, `renew_and_sync` por conta)
- Modify: `src/alerts/service.py` (gravações e envio sob `account_scope(admin_account_id)`; endereço vem da conta do admin)
- Modify: `src/services/sync.py` (nada de leitura global; recebe o escopo de quem chama)
- Test: `tests/test_api_scope.py`

**Interfaces:**
- Consumes: `account_for_user`, `connected_account_ids`, `admin_account_id` (Task 4); `read_access` (`src/api/security.py`), `ACCESS_COOKIE`
- Produces:
  - `class AccountScopeMiddleware` (ASGI puro, **não** `BaseHTTPMiddleware`): lê o cookie de acesso, resolve `user_id → account_id` e chama o app interno dentro de `account_scope(account_id)`; sem cookie válido segue sem escopo (rotas protegidas já devolvem 401).
  - `build_app`: `start_watch()` e a sincronização inicial percorrem `connected_account_ids(...)`, cada uma em `account_scope(id)`, falha de uma não interrompe as outras; o webhook continua disparando o `SyncCoordinator` único, que sincroniza cada conta conectada em seu escopo (roteamento por `emailAddress` é da fase 2).

- [ ] **Step 1: Write the failing tests** (`tests/test_api_scope.py`, `@pytest.mark.noscope`): `test_request_runs_in_user_account` (rota de lista da caixa autenticada como usuário 1 devolve só e-mails da conta 1, com e-mails de 2 no banco); `test_stream_keeps_scope` (rota SSE do chat lê a conversa da conta certa); `test_unauthenticated_has_no_scope` (webhook/healthz funcionam; rota protegida 401); `test_alert_goes_to_admin_account` (notificação gravada com `gmail_account_id` do admin, chamada feita fora de qualquer escopo).
- [ ] **Step 2: Run** `python -m pytest tests/test_api_scope.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `python -m pytest -q && python -m mypy` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(api): request, sync and alerts run in an account scope`

### Task 7: CLI com `--account`

**Files:**
- Modify: `src/cli/main.py` e os comandos em `src/cli/*.py` que tocam conteúdo (`backfill`, `classify_unread`, `reclassify`, `rules`, `actions`, `docs`, `jobs`, `enqueue`)
- Test: `tests/test_cli_account.py`

**Interfaces:**
- Consumes: `account_by_email`, `connected_account_ids`
- Produces: `resolve_cli_account(sf: sessionmaker[Session], email: str | None) -> int` em `src/cli/main.py` — com e-mail, a conta dele (erro se não existe); sem e-mail e exatamente uma conta, ela; senão erro `"informe --account <e-mail> (há N contas)"`. Cada comando de conteúdo roda em `account_scope(resolve_cli_account(...))`. `create_user`, `unlock` não mudam.

- [ ] **Step 1: Write the failing tests**: `test_single_account_is_default`, `test_requires_account_when_many` (mensagem exata acima com N=2), `test_unknown_account_errors`.
- [ ] **Step 2: Run** `python -m pytest tests/test_cli_account.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `python -m pytest -q && python -m mypy` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(cli): --account selects the account a command runs in`

### Task 8: Testes de isolamento da API (duas contas)

**Files:**
- Create: `tests/test_isolation.py`
- Modify: `tests/api_fakes.py` (fixture que semeia duas contas com: e-mail, anexo, ação sugerida, regra, entrada de KB, conversa com mensagem, job, notificação)

**Interfaces:**
- Consumes: fixtures `second_account`, `AccountScopeMiddleware`

- [ ] **Step 1: Write the tests** (`@pytest.mark.noscope`, cliente autenticado como usuário 1):
  - `test_lists_show_only_own_data` — parametrizado por cada rota GET de lista em `src/api/routes/*.py` (caixa, relevantes, documentos, aprovações, regras, KB, conversas, execuções, notificações): nenhum item da conta 2.
  - `test_detail_of_other_account_is_404` — parametrizado por cada rota de detalhe/ação com id (e-mail, anexo/download, ação aprovar/rejeitar, conversa, regra): usando o id da conta 2 → 404.
  - `test_chat_tools_never_see_other_account` — `list_emails`/busca do chat rodando no escopo 1 não devolvem e-mail da conta 2.
- [ ] **Step 2: Run** `python -m pytest tests/test_isolation.py -v` — Expected: PASS (Tasks 1-6 já entregam isso); qualquer falha é bug a corrigir na rota antes de seguir.
- [ ] **Step 3: Commit** `test: cross-account isolation over every API route`

### Task 9: Migração `0021` + RLS + testes no Postgres

**Files:**
- Create: `alembic/versions/0021_multi_account.py`
- Create: `tests/test_multitenant_pg.py`
- Modify: `.github/workflows/ci.yml` (incluir o arquivo novo no passo "pytest on Postgres")

**Interfaces:**
- Consumes: esquema das Tasks 2-4
- Produces: revisão `"0021"`, `down_revision = "0020"`.

Upgrade, nesta ordem, numa transação com `SET LOCAL app.system = 'on'`:
1. Cria `gmail_accounts`, `account_settings`; `users` + `email`, `role`.
2. Se existe linha em `users`: `role='admin'`, `email` = `watch_state.email_address` (minúsculo); cria `gmail_accounts(id=1, user_id=<único user>, email_address, history_id, watch_expiration, status='connected')`; `account_settings(1)` a partir de `app_settings` (`llm_mode`, `backfill_paused`, `backfill_turbo`) e remove essas chaves de `app_settings`.
3. Remove linhas órfãs que impediriam as FKs compostas (referências a e-mail inexistente), registrando a contagem com `print` para o log do deploy.
4. Adiciona `gmail_account_id` (preenche `1`, depois `NOT NULL`) em todas as tabelas da Task 2; troca PKs/FKs/unicidades (Tasks 2-3).
5. Remove `watch_state`.
6. Para cada tabela de conteúdo: `ENABLE` + `FORCE ROW LEVEL SECURITY` e `CREATE POLICY account_isolation ... USING (<política das Global Constraints>) WITH CHECK (<mesma>)`.
Banco vazio (CI): passos 2-3 não fazem nada.

Downgrade: se `gmail_accounts` tem mais de 1 linha → `RuntimeError("downgrade de 0021 só com uma conta (há N)")`; senão desfaz na ordem inversa (recria `watch_state` e as chaves de `app_settings`).

- [ ] **Step 1: Write the failing tests** (`tests/test_multitenant_pg.py`, pulam sem `TEST_DATABASE_URL`; cada teste leva o banco para `0020`, semeia, sobe para `head` via API do Alembic):
  - `test_upgrade_keeps_counts` — conta linhas de todas as tabelas em `0020` (com um e-mail, anexo, embedding, ação, conversa, job, configurações) e compara depois do upgrade; `gmail_accounts` tem 1 linha com o e-mail do `watch_state`.
  - `test_migration_with_orphan_refs` — `chat_message_refs`/`llm_call_emails` apontando para e-mail inexistente: upgrade conclui; órfãos removidos.
  - `test_rls_blocks_forgotten_filter` — após upgrade, com duas contas: numa conexão crua com `SET LOCAL app.account_id = '1'`, `SELECT count(*) FROM emails` (SQL puro, sem o filtro ORM) devolve só as da conta 1; sem nenhuma variável, devolve 0.
  - `test_downgrade_refuses_with_two_accounts` — mensagem exata acima com N=2.
- [ ] **Step 2: Run** (WSL, stack local `gi-local` — ver memória "Docker via WSL") `TEST_DATABASE_URL=... python -m pytest tests/test_multitenant_pg.py -v` — Expected: FAIL (revisão inexistente).
- [ ] **Step 3: Implement** a migração.
- [ ] **Step 4: Run** `alembic upgrade head && alembic downgrade base && alembic upgrade head` e os testes PG (`tests/test_jobs_pg.py tests/test_vector_pg.py tests/test_multitenant_pg.py`) — Expected: tudo PASS; depois `python -m pytest -q && python -m mypy`.
- [ ] **Step 5: Commit** `feat(db): 0021 multi-account schema, data migration and RLS`

### Task 10: Deploy e verificação em produção

**Files:** nenhum no repo (corpo do PR registra os números).

- [ ] **Step 1: Pré-checagem na VPS** (PowerShell → SSH, ver memória "VPS produção"): contar órfãos que a migração vai apagar (refs de chat, itens de cartão, `llm_call_emails` sem e-mail) e contagem de linhas de cada tabela; anotar no PR.
- [ ] **Step 2: Backup** do Postgres antes do deploy (rollback de deploy não funciona após migração nova — restaurar backup é o caminho).
- [ ] **Step 3: PR + merge + deploy** (push via helper do gh — memória "Push via gh").
- [ ] **Step 4: Verificar**: contagens iguais às do Step 1 (menos órfãos); `SELECT * FROM gmail_accounts` = 1 linha com o e-mail do João; painel abre; um e-mail novo chega e é triado; chat responde; backfill continua de onde estava; `docker compose logs worker` sem `ScopeError`.
- [ ] **Step 5: Atualizar memória** do roadmap: fase 1 em produção.
