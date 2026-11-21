# Feedback de lotes no chat — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Lotes anunciados no cartão e na mensagem, progresso visível durante a execução e resposta do agente só com o resultado real.

**Spec:** `docs/superpowers/specs/2026-11-21-batch-feedback-design.md`

## Global Constraints

Pydantic, sem `Any`, sem JSON. Migrações 0019 (colunas) e 0020 (prompt v3). Verificação: `python -m pytest -q`, `python -m mypy`, `cd web && npx vitest run --maxWorkers=2 && npm run build`.

## Review Focus

1. Todas as ações do cartão executam antes de a espera ser gravada → a rota enfileira na hora (teste Task 2).
2. Job de execução morre → conta como falha e libera a continuação (teste Task 2).
3. Cartão recusado → continuação imediata, sem espera (teste Task 2).
4. Busca com estimativa 0 ou menor que o lote → "lote 1 de 1", sem "restam" (teste Task 1).

### Task 1: Metadados de lote no cartão

`chat_cards.batch_query`, `batch_total`, `batch_no` (0019); `ActionsDraft` ganha os três (opcionais); `CardView` expõe; `propose_email_actions` com `gmail_query` preenche (`batch_no` = cartões anteriores da conversa com a mesma busca e ação + 1; `batch_total` = `result_size_estimate` da primeira página) e a resposta da tool diz "lote N de M (K de ~T e-mails da busca 'q')". Testes em `tests/test_chat_tools.py`.

### Task 2: Continuação que espera a execução

`chat_messages.wait_card_id` (0019). `ChatStore.start_event_turn(..., wait_card_id=None)` grava a espera e o progresso inicial; `ChatStore.settle_card(card_id) -> int | None` conta os itens (`executed`/`failed`/`approved`), atualiza o progresso ("apagando… 12/50") e, quando não sobra `approved`, reescreve o texto do `[evento]` com o resultado, limpa a espera e devolve o id do assistente para enfileirar. `ChatStore.cards_of_action(action_id) -> list[int]`. `ExecuteActionFlow(on_settled=Callable[[int], None])` chama depois de executar/falhar; o `on_dead` do worker também. Rota `confirm`: ações aprovadas → `start_event_turn(wait_card_id=...)` + `settle_card` imediato. `card_event` passa a ter o texto final com contagens e lote. Testes em `tests/test_chat_store.py`, `tests/test_api_chat.py`, `tests/test_execute_action.py`.

### Task 3: Prompt v3 e painel

`CHAT_PROMPT_V3` (0020): ao propor um lote de uma busca maior, avisar "São ~T; vou dividir em M lotes de 50; este é o lote 1"; após `[evento]` de lote, dar o resultado ("Lote 1 concluído: 50 apagados") e oferecer o próximo lote se restar. Web: cabeçalho "Lote N de M · K de ~T" no cartão de ações. Testes: migração, `web/test/chat.test.tsx`.

### Task 4: Entrega

Suítes, PR, CI, merge, deploy, conferir.
