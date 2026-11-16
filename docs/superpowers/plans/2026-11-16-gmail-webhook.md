# Gmail webhook + Postgres + Docker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (o João proibiu subagentes: execução inline). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cada e-mail novo na INBOX é classificado e salvo em Postgres automaticamente via Gmail watch → Pub/Sub push → `POST /webhooks/gmail` (FastAPI), tudo sobe com `docker compose up -d`.

**Architecture:** `api.py` recebe o push e dispara em background um sync (`sync.py`): `history.list` a partir do cursor salvo → ingere resumos → classifica com o `Classifier` existente → grava. `store.py` é reescrito sobre SQLAlchemy mantendo a interface pública. Alembic cria o schema; compose sobe `db`, `migrate`, `api`.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, SQLAlchemy 2.x, Alembic, psycopg 3, pytest, Docker Compose, Postgres 16.

**Spec:** `docs/superpowers/specs/2026-11-16-gmail-webhook-design.md`

**Nota:** o diretório não é repositório git, então não há passos de commit. Cada task termina em verificação (`pytest` verde). Rode tudo com o interpretador do `.venv` (`.venv\Scripts\python`).

## Global Constraints

- Python 3.12; código plano na raiz, estilo atual (sem pacote `app/`).
- `POST /webhooks/gmail` (FastAPI). Handler responde `204` imediatamente; trabalho em background.
- Payload malformado → `204` (log, descarta). Token: se `WEBHOOK_TOKEN` definido exige `?token=` (comparação em tempo constante), senão `401`; ausente = aceita tudo.
- O `historyId` do payload é **ignorado**; vale o cursor salvo no banco.
- Watch: `labelIds=["INBOX"]`, `labelFilterBehavior="INCLUDE"`, `topicName` de env `GMAIL_PUBSUB_TOPIC` (`projects/<id>/topics/<nome>`). Renovação a cada 24 h; falha → nova tentativa em 1 h. **1 worker** uvicorn.
- Fallback de history 404: `in:inbox newer_than:7d` + cursor = `historyId` de `users.getProfile`.
- Webhook usa `Run` permanente com `query="webhook:inbox"`.
- SQLAlchemy 2.x declarativo, driver `psycopg` (`postgresql+psycopg://`), `DATABASE_URL` por env (sem default; `RuntimeError` se ausente).
- Compose: `db` (postgres:16, volume nomeado, healthcheck, sem porta publicada), `migrate` (`alembic upgrade head`), `api` (porta `127.0.0.1:8000:8000`, `env_file: .env`, volume `./secrets:/app/secrets` leitura/escrita, `restart: unless-stopped`).
- `secrets/`, `.env`, `credentials.json`, `emails.db*` nunca entram na imagem.
- Sem migração de dados do `emails.db`. Criar tópico/assinatura Pub/Sub e editar nginx: fora do escopo (João faz).

## Review Focus

1. Renovar o watch **não** pode mover o cursor `history_id` salvo (senão e-mails entre o cursor e o novo id se perdem). Testado na Task 4.
2. Mesma notificação duas vezes / triggers concorrentes → sem linhas duplicadas e sem classificar duas vezes. Task 5.
3. Mensagem apagada entre `history.list` e `messages.get` (404) → pula, não derruba o sync. Task 5.
4. `historyId` expirado (404 em `history.list`) → fallback de 7 dias e cursor reiniciado. Task 5.
5. LLM indisponível (`classify.Abort`) → e-mails ficam pendentes, cursor avança, próximo sync os classifica. Task 5.

---

### Task 1: Dependências, models e sessão

**Files:**
- Modify: `requirements.txt`
- Create: `models.py`, `db.py`, `tests/__init__.py`, `tests/conftest.py`, `pytest.ini`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces:
  - `models.Base`; `models.Run(id, query, page_token, pages, fetch_done, created_at)`; `models.Email(id, sender, subject, date, snippet, category, reason, attempts, last_error, updated_at)`; `models.RunEmail(run_id, email_id, added_at)` (PK composta run_id+email_id); `models.WatchState(id=1, email_address, history_id: str, expiration: BigInteger)`.
  - `db.get_database_url() -> str`; `db.make_session_factory(url: str | None = None) -> sessionmaker[Session]` (para `sqlite:///:memory:` usa `StaticPool` + `check_same_thread=False`; `pool_pre_ping=True`).
  - fixture `tests/conftest.py::session_factory` → `make_session_factory("sqlite:///:memory:")` com `Base.metadata.create_all`.

- [ ] **Step 1: Instalar dependências no `.venv` e fixar versões.** `pip install fastapi "uvicorn[standard]" sqlalchemy alembic "psycopg[binary]" pytest httpx`. Em `requirements.txt` manter as linhas atuais e **acrescentar** (versões instaladas, `pip show`) as diretas que faltam: fastapi, uvicorn, sqlalchemy, alembic, psycopg[binary], langchain, langchain-core, langchain-google-genai, python-dotenv, pydantic, pytest, httpx. Não usar `pip freeze` (o `.venv` tem pacotes vertex/bigquery sem uso).
- [ ] **Step 2: Teste falhando** em `tests/test_models.py`: `test_tables_created(session_factory)` insere `Run(query="q")`, `Email(id="a")`, `RunEmail(run_id=1, email_id="a")`, `WatchState(id=1, email_address="x@y", history_id="10", expiration=1)` e relê; `test_get_database_url_requires_env(monkeypatch)` espera `RuntimeError` sem `DATABASE_URL`.
- [ ] **Step 3: Rodar** `pytest tests/test_models.py -v` → FAIL (módulos não existem).
- [ ] **Step 4: Implementar** `models.py` (tipos genéricos portáveis sqlite/postgres: `String`/`Text`, `Integer`, `Boolean`, `DateTime(timezone=True)` com `server_default=func.now()`) e `db.py` conforme Interfaces. `pytest.ini`: `testpaths = tests`.
- [ ] **Step 5: Rodar** `pytest tests/test_models.py -v` → PASS.

### Task 2: `store.py` sobre SQLAlchemy

**Files:**
- Modify: `store.py` (reescrever), `classify.py:` só se algum call-site quebrar
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `models.*`, `db.make_session_factory`.
- Produces: `Store(session_factory=None)` (None → `make_session_factory()`; **não** cria schema, Alembic faz isso). Todos os métodos retornam `dict`/`list[dict]` (chaves = colunas; assim `row["id"]` segue funcionando em `classify.py`):
  - `get_or_create_run(query: str, force_new: bool = False) -> dict` (mesma regra de retomada de hoje)
  - `get_run(run_id: int) -> dict`; `reset_cursor(run_id: int) -> None`
  - `known_ids(ids: list[str]) -> set[str]`
  - `save_page(run_id, all_ids: list[str], new_emails: list[dict], next_token: str | None) -> None` (chaves de `new_emails`: `id, from, subject, date, snippet`)
  - `ingest(run_id, all_ids, new_emails) -> None` (igual a `save_page` sem tocar `runs`)
  - `count_pending(run_id) -> int`; `pending(run_id, limit) -> list[dict]` (ordem: `attempts`, `RunEmail.added_at`, `Email.id`)
  - `save_classifications(results: Iterable[tuple[str, str, str]]) -> None`; `bump_attempts(ids, error) -> None`; `give_up(max_attempts, category, reason) -> None`; `report_rows(run_id) -> list[dict]`
  - `get_watch_state() -> dict | None` (`email_address, history_id, expiration`)
  - `init_watch_state(email_address: str, history_id: str, expiration: int) -> None` — insere se não existe; se existe, atualiza **só** `email_address` e `expiration` (nunca `history_id`)
  - `set_history_id(history_id: str) -> None`; `close() -> None`
- `INSERT ... ON CONFLICT DO NOTHING` via `sqlalchemy.dialects.postgresql.insert` ou `sqlite.insert` conforme `session.bind.dialect.name`.

- [ ] **Step 1: Testes falhando** em `tests/test_store.py` (usam `Store(session_factory)`): `test_save_page_idempotent` (2× o mesmo `save_page` → 1 linha em `emails`, `pages == 2`, `fetch_done` vira `True` com `next_token=None`); `test_get_or_create_run_resumes_unfinished`; `test_pending_orders_by_attempts` (e-mail com `bump_attempts` vem depois); `test_save_classifications_clears_pending`; `test_give_up_marks_nao_classificado`; `test_init_watch_state_keeps_history_id` (insere `("a@b","100",1)`, chama `("a@b","999",2)` → `history_id == "100"`, `expiration == 2`); `test_set_history_id`.
- [ ] **Step 2: Rodar** `pytest tests/test_store.py -v` → FAIL.
- [ ] **Step 3: Reescrever `store.py`** conforme Interfaces, uma sessão curta por método (`with self.sf.begin() as s`), convertendo linhas em `dict`.
- [ ] **Step 4: Rodar** `pytest tests/test_store.py -v` → PASS; `python -c "import classify, main"` sem erro (call-sites compatíveis).

### Task 3: Alembic

**Files:**
- Create: `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/0001_initial.py`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Consumes: `models.Base.metadata`, `db.get_database_url()`.
- Produces: `alembic upgrade head` cria `runs`, `emails`, `run_emails`, `watch_state`; `downgrade base` remove.

- [ ] **Step 1: Teste falhando** `test_upgrade_and_downgrade(tmp_path, monkeypatch)`: `DATABASE_URL=sqlite:///<tmp>/t.db`, `alembic.command.upgrade(Config("alembic.ini"), "head")` → `inspect(engine).get_table_names()` contém as 4 tabelas; `downgrade base` → só `alembic_version`.
- [ ] **Step 2: Rodar** → FAIL.
- [ ] **Step 3: `alembic init alembic`**, ajustar `env.py` para ler `DATABASE_URL` (via `db.get_database_url()`), `target_metadata = Base.metadata`. Escrever `0001_initial.py` **à mão** espelhando `models.py` (sem autogenerate).
- [ ] **Step 4: Rodar** `pytest tests/test_migrations.py -v` → PASS.

### Task 4: `watch.py`

**Files:**
- Create: `watch.py`
- Test: `tests/test_watch.py`

**Interfaces:**
- Consumes: `Store.init_watch_state`, `Store.get_watch_state`.
- Produces: `start_gmail_watch(service) -> dict` (retorna `{"historyId": str, "expiration": str}`; levanta `RuntimeError` se `GMAIL_PUBSUB_TOPIC` ausente); `ensure_watch(store: Store, service) -> dict` (chama `start_gmail_watch`, `users().getProfile(userId="me")` para o e-mail, `store.init_watch_state(email, historyId, int(expiration))`, retorna a resposta do watch).

- [ ] **Step 1: Testes falhando** com `FakeService` (objeto que grava as chamadas `users().watch(...)`/`getProfile`): `test_watch_body` (body exato: `topicName` do env, `labelIds == ["INBOX"]`, `labelFilterBehavior == "INCLUDE"`); `test_missing_topic_raises`; `test_ensure_watch_first_run_saves_cursor`; `test_renew_does_not_move_cursor` (Review Focus 1: 2ª chamada com `historyId` maior → `get_watch_state()["history_id"]` continua o da 1ª, `expiration` atualizada).
- [ ] **Step 2: Rodar** → FAIL.
- [ ] **Step 3: Implementar** conforme Interfaces.
- [ ] **Step 4: Rodar** `pytest tests/test_watch.py -v` → PASS.

### Task 5: `sync.py`

**Files:**
- Create: `sync.py`
- Test: `tests/test_sync.py`

**Interfaces:**
- Consumes: `Store` (Task 2), `classify._summary(service, message_id) -> dict`, `classify.classify_pending(store, classifier, run_id)`, `classify.Abort`.
- Produces:
  - `WEBHOOK_QUERY = "webhook:inbox"`; `class HistoryExpired(Exception)`
  - `list_new_message_ids(service, start_history_id: str) -> tuple[list[str], str]` — `history.list(userId="me", startHistoryId=..., historyTypes=["messageAdded"], labelId="INBOX")`, pagina por `nextPageToken`, devolve ids sem repetição e o `historyId` da última página; `HttpError` 404 → `HistoryExpired`.
  - `list_recent_inbox_ids(service, days: int = 7) -> tuple[list[str], str]` — `messages.list(q=f"in:inbox newer_than:{days}d")` paginado + `getProfile()["historyId"]`.
  - `sync_inbox(store: Store, service, classifier) -> dict` retorna `{"ingested": int, "pending": int}`. Ordem: estado do watch (ausente → `RuntimeError`) → ids (fallback em `HistoryExpired`) → `known_ids` → `_summary` só dos novos (404 → pula) → `store.ingest(run_id, ids, novos)` → `store.set_history_id(latest)` → `classify_pending` (captura `Abort`, loga). O `set_history_id` vem logo após `ingest`: depois disso os e-mails já estão duráveis e o retry de pendentes cobre a classificação.
  - `class SyncCoordinator(run_fn: Callable[[], None])` com `trigger() -> None`: se já há execução, marca `dirty` e retorna; quem está rodando repete enquanto `dirty`. Exceção de `run_fn` é logada e não derruba o loop.

- [ ] **Step 1: Testes falhando** com `FakeService` roteirizável e `FakeClassifier`: `test_ingests_and_classifies_new_message`; `test_duplicate_notification_no_duplicates` (rodar `sync_inbox` 2× com mesmo history → 1 linha, classificador chamado 1×) [FR2]; `test_deleted_message_is_skipped` (`messages.get` levanta `HttpError` 404 para um id → os outros são ingeridos) [FR3]; `test_history_expired_falls_back` (history 404 → usa `list_recent_inbox_ids`, cursor = historyId do perfil) [FR4]; `test_llm_abort_keeps_pending_and_advances_cursor` (classificador levanta `Abort` → `count_pending > 0`, cursor avançou; 2º sync com classificador ok → 0 pendentes) [FR5]; `test_coordinator_coalesces` (trigger concorrente de 3 threads com `run_fn` lento → `run_fn` roda ≥1 e ≤ 3 vezes, nunca em paralelo — contador de concorrência máxima == 1; trigger durante execução garante uma rodada posterior).
- [ ] **Step 2: Rodar** `pytest tests/test_sync.py -v` → FAIL.
- [ ] **Step 3: Implementar** conforme Interfaces.
- [ ] **Step 4: Rodar** `pytest tests/test_sync.py -v` → PASS.

### Task 6: `api.py`

**Files:**
- Create: `api.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `SyncCoordinator.trigger`, `watch.ensure_watch`, `Store`, `get_gmail_service`, `classify.Classifier`.
- Produces:
  - `decode_push(body: object) -> dict | None` — decodifica `message.data` (base64 → JSON); devolve `{"emailAddress", "historyId"}` ou `None` se malformado.
  - `create_app(*, trigger: Callable[[], None], token: str | None, on_startup: Callable[[], None] | None = None, renew: Callable[[], None] | None = None, renew_interval_s: float = 86400, retry_s: float = 3600) -> FastAPI` — rotas `POST /webhooks/gmail` (204; 401 se token exigido e errado; `trigger` via `BackgroundTasks` só quando `decode_push` válido) e `GET /healthz` (200 `{"status": "ok"}`); lifespan roda `on_startup` em thread (exceção logada, não fatal) e inicia loop de renovação (`renew` a cada `renew_interval_s`; falha → espera `retry_s`), cancelado no shutdown.
  - `build_app() -> FastAPI` — liga tudo pelo ambiente (`Store()`, um `Classifier` compartilhado criado lazy, `SyncCoordinator(run_fn)` onde `run_fn` cria `get_gmail_service()` **novo por execução** e chama `sync_inbox`; `on_startup = ensure_watch`; `renew = ensure_watch + trigger`; `token = os.getenv("WEBHOOK_TOKEN")`). Usado com `uvicorn api:build_app --factory`.

- [ ] **Step 1: Testes falhando** (`TestClient`, `trigger` = contador): `test_valid_push_returns_204_and_triggers`; `test_malformed_body_returns_204_without_trigger` (corpo não-JSON, `data` não-base64, JSON sem `historyId`); `test_wrong_token_401` / `test_right_token_204` / `test_no_token_configured_accepts_all`; `test_healthz`; `test_renew_loop_retries_after_failure` (`renew` falha 1ª vez, `renew_interval_s=0.01`, `retry_s=0.01` → chamado ≥2×); `test_startup_failure_not_fatal` (`on_startup` levanta → app sobe, `/healthz` 200).
- [ ] **Step 2: Rodar** → FAIL.
- [ ] **Step 3: Implementar** conforme Interfaces; comparar token com `hmac.compare_digest`.
- [ ] **Step 4: Rodar** `pytest -v` (suíte inteira) → PASS.

### Task 7: Docker, compose, env e README

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.env.example`, `README.md`
- Modify: `.gitignore` (nada novo necessário se `.env` já ignorado — conferir)

**Interfaces:**
- Consumes: `api:build_app`, `alembic.ini`.
- Produces: `docker compose up -d --build` sobe os 3 serviços.

- [ ] **Step 1: `Dockerfile`**: `python:3.12-slim`, `pip install --no-cache-dir -r requirements.txt`, copiar código, usuário não-root, `CMD ["uvicorn", "api:build_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]`.
- [ ] **Step 2: `docker-compose.yml`** conforme Global Constraints. `DATABASE_URL: postgresql+psycopg://gmail:${POSTGRES_PASSWORD}@db:5432/gmail` em `migrate` e `api`; `api.depends_on.migrate.condition: service_completed_successfully`; `migrate.depends_on.db.condition: service_healthy`; healthcheck do `api` com `python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"`.
- [ ] **Step 3: `.dockerignore`** (`.venv`, `secrets`, `.env`, `credentials.json`, `emails.db*`, `__pycache__`, `docs`, `tests`) e **`.env.example`** (`GOOGLE_API_KEY`, `POSTGRES_PASSWORD`, `GMAIL_PUBSUB_TOPIC`, `WEBHOOK_TOKEN` opcional; sem valores reais).
- [ ] **Step 4: `README.md`** em pt-BR com: visão do fluxo; deploy (`scp secrets/token.json ubuntu@<SEU_DOMINIO>:<dir>/secrets/`, copiar `.env`, `docker compose up -d --build`); snippet nginx da spec; como ligar `WEBHOOK_TOKEN` e trocar o endpoint da assinatura para `?token=`; CLI: `docker compose run --rm api python main.py`; nota de 1 worker; pedir para atualizar `requirements.txt` ao mudar deps.

### Task 8: Verificação ponta a ponta local

**Files:** nenhum novo.

- [ ] **Step 1:** copiar `.env.example` → `.env` temporário de teste (ou usar o existente + `POSTGRES_PASSWORD`), `GMAIL_PUBSUB_TOPIC=projects/x/topics/y`, `WEBHOOK_TOKEN=t`.
- [ ] **Step 2:** `docker compose up -d --build` → `docker compose ps` mostra `db` healthy, `migrate` exited 0, `api` up (sem `token.json` o `ensure_watch` falha no startup: o log mostra o erro e a API segue de pé).
- [ ] **Step 3:** `curl -i http://127.0.0.1:8000/healthz` → 200; `curl -i -X POST http://127.0.0.1:8000/webhooks/gmail -d '{}'` → 401; com `?token=t` e corpo `{}` → 204; com `?token=t` e corpo válido (`{"message":{"data":"<base64 de {\"emailAddress\":\"a@b\",\"historyId\":\"1\"}>"}}`) → 204.
- [ ] **Step 4:** `docker compose exec db psql -U gmail -c "\dt"` lista `runs, emails, run_emails, watch_state, alembic_version`.
- [ ] **Step 5:** `docker compose down` (sem `-v`) e rodar `pytest -v` final → tudo verde. Reportar ao João o resultado real, incluindo qualquer passo que não deu para verificar (ex.: Gmail real, sem `token.json` no ambiente de teste).
