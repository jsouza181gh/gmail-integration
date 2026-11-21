# Duas chaves de API — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** O backfill roda numa chave própria e numa faixa própria do worker; o app (chat, e-mail novo) nunca disputa cota nem fila com ele; um botão de turbo deixa o backfill usar também a chave do app quando ela está folgada.

**Architecture:** `LLMGateway` recebe a chave; `build_gateway`/`build_chat_gateway` recebem a chave do app e `build_backfill_gateway` a do backfill. Um `BackfillLLM` (roteador) implementa `StructuredGenerator` + `Embedder` sobre o gateway do backfill e, com turbo, alterna com o gateway do app quando este não está sob pressão. Novo tipo de job `backfill_attachment`; o worker ganha a faixa do backfill.

**Tech Stack:** Python 3.13, SQLAlchemy/Alembic, Pydantic v2, langchain-google-genai, google-genai, FastAPI; React/TS.

**Spec:** `docs/superpowers/specs/2026-11-21-two-api-keys-design.md`

## Global Constraints

- Pydantic em tudo; `Any` proibido; sem JSON/JSONB.
- Variáveis: `GOOGLE_API_KEY` (app), `GOOGLE_API_KEY_BACKFILL` (backfill; vazia → usa a do app).
- Setting: `app_settings.backfill_turbo` (`"1"` = ligado); padrão desligado.
- Faixas: chat = `{chat_turn}`; backfill = `{backfill_page, backfill_attachment}`; app = o resto.
- Verificação: `python -m pytest -q`, `python -m mypy`, `cd web && npx vitest run --maxWorkers=2 && npm run build`.

## Review Focus

1. Sem `GOOGLE_API_KEY_BACKFILL` → backfill e app usam a mesma chave e o turbo não muda nada (botão desabilitado). Teste em Task 2/4.
2. Turbo ligado com o gateway do app sob pressão (modelo pausado ou sem cota) → backfill não chama a chave do app. Teste em Task 3.
3. Turbo ligado e a chave do backfill sem cota do dia → backfill segue só pela do app enquanto ela estiver folgada; quando ambas indisponíveis, `LLMExhaustedError` com `quota` (o job adia). Teste em Task 3.
4. Anexo de e-mail novo enfileirado como `index_attachment`, do histórico como `backfill_attachment`; os dois idempotentes. Teste em Task 1.
5. Job `backfill_attachment` antigo não existe; `index_attachment` já enfileirados do backfill antes do deploy continuam na faixa do app (aceito). Sem teste.

---

### Task 1: Tipo de job `backfill_attachment`

**Files:** `src/jobs/types.py`, `src/jobs/store.py`, `src/db/models.py` (constraint), `alembic/versions/0018_backfill_attachment_job.py`, `src/indexing/indexer.py`, `src/indexing/documents.py`; tests `tests/test_jobs.py`, `tests/test_indexer.py`, `tests/test_documents.py`, `tests/test_migrations.py`.

**Interfaces:** `BackfillAttachmentJob(kind="backfill_attachment", attachment_id: int)`, prioridade 1, chave `backfill_attachment:{id}`. `Indexer.index(ids, run_id)` enfileira `BackfillAttachmentJob` quando `run_id is not None`, senão `IndexAttachmentJob`. `DocumentIndexer.handle` aceita os dois.

- [ ] Testes RED: roundtrip do job no `JobStore` (claim devolve o payload certo); `index(..., run_id=7)` enfileira `backfill_attachment`, `index(..., run_id=None)` enfileira `index_attachment`; `DocumentIndexer.handle` com `BackfillAttachmentJob` analisa o anexo; migração 0018 cria a constraint e o downgrade remove.
- [ ] Implementar; suíte + mypy; commit `feat(jobs): backfill_attachment job kind`.

### Task 2: Chave por gateway

**Files:** `src/llm/gateway.py` (`api_key` em `LLMGateway`, `chat_client_factory`, `GenAIEmbedder`), `src/llm/config.py` (`load_keys`, `build_gateway(key=...)`, `build_chat_gateway(key=...)`, `build_backfill_gateway`), `.env.example`; tests `tests/test_llm_gateway.py`.

**Interfaces:** `ApiKeys(Schema): app: str; backfill: str; separate: bool` de `load_keys()` (`separate` = a do backfill foi definida e é diferente). `LLMGateway(..., api_key: str | None = None)` repassa a chave para `ChatGoogleGenerativeAI(google_api_key=...)` e `genai.Client(api_key=...)`; `None` = variável de ambiente (comportamento atual).

- [ ] Testes RED: `load_keys` com e sem `GOOGLE_API_KEY_BACKFILL`; o cliente criado pelo gateway do backfill carrega a chave do backfill (`client.google_api_key`).
- [ ] Implementar; suíte + mypy; commit `feat(llm): one gateway per API key`.

### Task 3: Roteador do backfill com turbo

**Files:** `src/llm/backfill.py` (novo), `src/llm/gateway.py` (`under_pressure()`), `src/agent/settings.py` (`backfill_turbo`, `set_backfill_turbo`, `turbo_source`); tests `tests/test_backfill_llm.py`, `tests/test_settings.py` (ou onde está o teste do pause).

**Interfaces:** `LLMGateway.under_pressure() -> bool` (algum modelo da cadeia pausado ou sem cota). `BackfillLLM(backfill: LLMGateway, app: LLMGateway | None, turbo: Callable[[], bool])` implementa `generate_structured` e `embed`: turbo desligado ou `app is None` → só backfill; ligado → alterna (backfill, app, backfill…), pulando o app quando `app.under_pressure()`; `LLMExhaustedError` de cota num → tenta o outro (se permitido) antes de propagar.

- [ ] Testes RED: turbo off → só backfill; turbo on → alterna; app sob pressão → nunca chama o app; backfill sem cota + app folgado → usa o app; ambos sem → `LLMExhaustedError(kinds=("quota",))`; `set_backfill_turbo` grava/audita e `backfill_turbo()` lê.
- [ ] Implementar; suíte + mypy; commit `feat(backfill): turbo router over the two keys`.

### Task 4: Wiring, faixa do backfill, API e botão

**Files:** `src/indexing/wiring.py` (gateway injetado), `src/worker.py` (três faixas; `Indexing` do app com o gateway do app e do backfill com `BackfillLLM`), `src/api/routes/ops.py` (`BackfillStatus.turbo`, `turbo_available`; `POST /api/backfill/turbo {on: bool}`), `web/src/api/*`, `web/src/pages/Execucoes.tsx` (botão Turbo ao lado de Pausar); tests `tests/test_api_ops.py`, `web/test/execucoes.test.tsx`.

- [ ] Testes RED: `POST /api/backfill/turbo` liga/desliga e o status devolve `turbo`; `turbo_available` falso sem a segunda chave; web: botão "Ligar turbo"/"Desligar turbo" chama a rota e fica desabilitado com `turbo_available=false`.
- [ ] Implementar; suíte, mypy, vitest, build; commit `feat(panel): backfill turbo toggle`.

### Task 5: Entrega

- [ ] PR; após o merge e deploy, João põe `GOOGLE_API_KEY_BACKFILL` no `.env` da VPS e reinicia o worker; conferir em `llm_calls` que `backfill_page` e chat respondem e que o botão aparece habilitado.
