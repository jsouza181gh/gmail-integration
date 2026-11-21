# Chat utilizável — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** O chat consegue mostrar e propor ações em qualquer e-mail do Gmail (inclusive spam), em lote, retoma sozinho depois de um cartão confirmado, lembra quais e-mails citou e roda numa cadeia de modelos flash com fallback.

**Architecture:** Ids desconhecidos viram linhas mínimas `emails.source='chat'` sob demanda (`EmailLookup.ensure`). Uma tool de lote gera um cartão só. Confirmar cartão grava uma mensagem `input='event'` e enfileira `chat_turn`. Cada resposta grava `chat_message_refs`, que o histórico renderiza. Um segundo `LLMGateway` (CHAT_MODELS) atende o chat; o gateway passa a lembrar modelos sem cota do dia.

**Tech Stack:** Python 3.13, SQLAlchemy 2 + Alembic, Pydantic v2, LangChain + langchain-google-genai, FastAPI; React/TS + TanStack Query (web/).

**Spec:** `docs/superpowers/specs/2026-11-20-chat-usable-design.md`

## Global Constraints

- Tudo tipado em Pydantic; `Any` proibido (mypy strict + `disallow_any_explicit`).
- Sem colunas JSON/JSONB.
- `messages.delete` proibido; enviar/responder/filtros/`block_sender` nunca `auto`; nada muda estado no Gmail sem aprovação (o chat só propõe).
- Conteúdo de terceiros sempre cercado por `<email_data>` (`fenced`).
- Textos ao usuário em pt-BR.
- Cadeia padrão: `CHAT_MODELS=gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite`.
- Reset da cota diária: meia-noite `America/Los_Angeles`.
- Assinatura neutra ao trocar de modelo: `skip_thought_signature_validator`.
- Lote: no máximo 50 e-mails por cartão (`BATCH_MAX = 50`); refs: 20 por mensagem gravadas, 10 mostradas no histórico.
- Verificação: `pytest`, `mypy src tests`, `cd web && npm test && npm run build`.

## Review Focus

1. Id que o Gmail não conhece mais (404) chegando em `ensure` → tool responde "esse e-mail não existe mais no Gmail", nunca exceção crua. Teste em Task 2.
2. Lote misto (válidos, inexistentes, duplicados, negados pela política) → um cartão só com os válidos e duplicados; a resposta da tool conta cada grupo. Teste em Task 3.
3. Usuário manda mensagem nova enquanto a continuação seria criada (conversa ocupada) → confirmação funciona e nenhuma continuação é criada. Teste em Task 5.
4. E-mail `source='chat'` que depois chega na INBOX (usuário tirou do spam) → linha vira `inbox` e aparece no painel. Teste em Task 1.
5. Todos os modelos da cadeia sem cota → turno falha com a mensagem das 05h, sem dormir 30 min. Teste em Task 7.

---

### Task 1: Esquema — `emails.source` e `chat_message_refs`

**Files:**
- Create: `alembic/versions/0015_chat_sources_refs.py`
- Modify: `src/db/models.py` (classe `Email`; nova `ChatMessageRef`), `src/db/rows.py` (`EmailRow.source`), `src/db/store.py` (`_ingest`)
- Modify: `src/api/queries.py` (listas da Caixa e Relevantes filtram `Email.source == "inbox"`)
- Test: `tests/test_migrations.py`, `tests/test_models.py`, `tests/test_api_mail.py`

**Interfaces:**
- Produces: `Email.source: Mapped[str]` (`String(16)`, not null, server_default `'inbox'`, valores `inbox|chat`); `ChatMessageRef(message_id FK chat_messages.id, position int, email_id FK emails.id, subject Text|None, sender Text|None)`, PK `(message_id, position)`; `EmailRow.source: str`.

- [ ] **Step 1: Testes que falham**
  - `test_migration_0015_upgrade_downgrade`: upgrade cria a coluna (linhas antigas = `inbox`) e a tabela; downgrade remove.
  - `test_ingest_promotes_chat_row_to_inbox`: linha `source='chat'` + `Store.ingest(...)` com o mesmo id → `source == 'inbox'`, demais campos preservados.
  - `test_mail_list_hides_chat_rows`: `GET /api/mail` (e `/relevant`) não devolve linha `source='chat'`.
- [ ] **Step 2:** `pytest tests/test_migrations.py tests/test_models.py tests/test_api_mail.py -v` → FAIL.
- [ ] **Step 3: Implementar.** `_ingest` continua com insert-ignore e em seguida faz `UPDATE emails SET source='inbox' WHERE id IN (:ids) AND source='chat'`.
- [ ] **Step 4:** mesmos testes → PASS; `mypy src tests` limpo.
- [ ] **Step 5: Commit** `feat(db): email source and chat message refs`

### Task 2: E-mail sob demanda (`EmailLookup.ensure`)

**Files:**
- Modify: `src/gmail/reader.py` (`GmailReader.item`), `src/agent/ports.py` (`MailReader.item`, `EmailLookup.ensure`), `src/chat/retrieval.py` (`DbEmailLookup`), `src/agent/wiring.py`
- Modify: `src/agent/tools/chat_read.py` (`read_email`), `src/agent/tools/chat_cards.py` (`show_emails`)
- Modify: `tests/agent_fakes.py` (`NoMail.item`, `NoLookup.ensure`)
- Test: `tests/test_chat_tools.py`, `tests/test_gmail_reader.py`

**Interfaces:**
- Produces: `GmailReader.item(email_id: str) -> MailItem | None` (o `_item` atual vira público; `HttpError` 404 → `None`). `EmailLookup.ensure(email_id: str) -> EmailRow | None`: banco primeiro; senão `mail.item` → insere `Email(id, sender, subject, date, snippet, received_at=_parse_date(date), source="chat")` (insert-ignore) e devolve a linha; `None` quando o Gmail não conhece. `DbEmailLookup(sf, mail: MailReader)`.
- Consumes: `Email.source` (Task 1).

- [ ] **Step 1: Testes que falham**
  - `test_item_404_returns_none` (reader com service fake que levanta `HttpError` 404).
  - `test_ensure_fetches_and_stores_unknown_id`: lookup sem a linha + mail fake com `MailItem(id="sp1", sender="Google Alerts <googlealerts-noreply@google.com>", ...)` → `ensure("sp1").source == "chat"` e a linha existe no banco.
  - `test_ensure_unknown_in_gmail_returns_none`.
  - `test_show_emails_accepts_spam_ids`: `show_emails(["sp1","sp2"])` com ids fora do banco → `"cartão #… 2 e-mail(s)"`.
  - `test_show_emails_gone_id_message`: id que o Gmail não conhece → mensagem contém `"não existe mais no Gmail"`.
- [ ] **Step 2:** `pytest tests/test_chat_tools.py tests/test_gmail_reader.py -v` → FAIL.
- [ ] **Step 3: Implementar.** `show_emails` e `read_email` trocam `lookup.email` por `lookup.ensure`; `read_email` de linha `chat` busca o corpo via `mail.get_body` (já existe).
- [ ] **Step 4:** testes → PASS; `mypy src tests`.
- [ ] **Step 5: Commit** `feat(chat): fetch unknown emails from Gmail on demand`

### Task 3: Ações em lote (`propose_email_actions`)

**Files:**
- Modify: `src/agent/tools/chat_cards.py` (remove `propose_email_action`, adiciona `propose_email_actions`), `src/agent/tools/__init__.py`/`src/agent/registry.py` (registro do chat), `src/agent/prompts.py` (`TOOL_LABELS`)
- Test: `tests/test_chat_tools.py`

**Interfaces:**
- Produces:
  ```python
  BATCH_MAX = 50
  def propose_email_actions(kind: ActionKind, reason: str, runtime: ToolRuntime[ProposeActionCtx, NoState],
                            email_ids: list[str] | None = None, gmail_query: str | None = None,
                            ) -> tuple[str, StateUpdate | None]
  ```
  Exatamente um entre `email_ids` e `gmail_query` (senão `ValueError("informe email_ids ou gmail_query")`). `gmail_query` → `mail.list_messages(MailQuery(query=..., max_results=BATCH_MAX, include_spam_trash=True))` paginando até 50. Cada id passa por `lookup.ensure`; `actions.propose(..., trigger="chat")`. Um `ActionsDraft(action_ids=...)` com `suggested` + `duplicate`. Resposta: `"cartão #N: <kind> em X e-mail(s); já pendentes: D; negados pela política: P; não existem mais no Gmail: G; WAITING"` (omitindo grupos zerados). `auto` conta como "executados pela política: A". Nenhum válido → `ValueError` com a mesma contagem.
  `ProposeActionCtx` ganha `mail: MailReader`.
- Consumes: `EmailLookup.ensure` (Task 2).

- [ ] **Step 1: Testes que falham**
  - `test_batch_one_card_for_many`: 5 ids → 1 cartão com 5 itens.
  - `test_batch_mixed_outcomes` (Review Focus 2): 1 válido, 1 duplicado, 1 sumido no Gmail, política `deny` para outro kind não interfere → texto contém `"já pendentes: 1"` e `"não existem mais no Gmail: 1"`, cartão com 2 itens.
  - `test_batch_by_gmail_query_caps_at_50`: mail fake com 120 resultados em 3 páginas → 50 ações.
  - `test_batch_requires_exactly_one_source`.
  - `test_propose_email_action_is_gone`: o registro do chat não tem mais `propose_email_action`.
- [ ] **Step 2:** `pytest tests/test_chat_tools.py -v` → FAIL.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4:** PASS; `mypy src tests`.
- [ ] **Step 5: Commit** `feat(chat): batch email actions in a single card`

### Task 4: Referências no histórico

**Files:**
- Modify: `src/agent/ports.py` (`RefSink`), `src/chat/store.py` (`add_refs`, `history`), `src/agent/context.py` (`refs` no contexto), `src/agent/wiring.py`
- Modify: `src/agent/tools/chat_read.py` (`search_mail`), `src/agent/tools/chat_cards.py` (`show_emails`, `propose_email_actions`), `src/agent/tools/mail.py` (`list_emails`: grava só quando `runtime.context.chat` existe)
- Test: `tests/test_chat_store.py`, `tests/test_chat_tools.py`

**Interfaces:**
- Produces: `RefSink.add_refs(message_id: int, refs: Sequence[EmailRef]) -> None` com `EmailRef(Schema): email_id: str; subject: str | None; sender: str | None`. Ignora repetidos na mesma mensagem e corta em 20 (`REFS_MAX = 20`). `ChatStore.history` acrescenta à resposta do assistente a linha `"[e-mails desta resposta: <id> — <assunto> (<remetente>); …]"` com no máximo 10 (`REFS_SHOWN = 10`), depois das notas de cartão.
- Consumes: `ChatMessageRef` (Task 1).

- [ ] **Step 1: Testes que falham**
  - `test_add_refs_dedupes_and_caps`: 25 refs com 2 repetidos → 20 gravados, em ordem.
  - `test_history_renders_refs`: resposta com 12 refs → linha com 10 ids, formato acima.
  - `test_list_emails_records_refs_only_in_chat`: contexto de webhook (sem `chat`) não grava; contexto de chat grava.
- [ ] **Step 2:** `pytest tests/test_chat_store.py tests/test_chat_tools.py -v` → FAIL.
- [ ] **Step 3: Implementar.** Ids de `list_emails` vêm do Gmail sem linha no banco e `chat_message_refs.email_id` é FK: a tool chama `lookup.ensure` para cada id antes de `add_refs` (o store não conhece o Gmail); ids com `ensure → None` ficam de fora.
- [ ] **Step 4:** PASS; `mypy src tests`.
- [ ] **Step 5: Commit** `feat(chat): remember which emails each answer referenced`

### Task 5: Continuação após confirmar cartão

**Files:**
- Create: `src/chat/events.py`
- Modify: `src/chat/store.py` (`start_event_turn`, `card_conversation`), `src/api/routes/chat.py` (`confirm`)
- Modify: `web/src/api/schemas.ts` (`input` aceita `'event'`), `web/src/pages/Chat/useChat.ts` (`confirm` invalida `messagesKey(conversationId)` depois de `replaceCard`), `web/src/pages/Chat/index.tsx` (mensagem `input==='event'` vira nota centralizada pequena, cor secundária, não bolha)
- Test: `tests/test_chat_store.py`, `tests/test_api_chat.py`, `web/src/pages/Chat/*.test.tsx`

**Interfaces:**
- Produces: `card_event(view: CardView) -> str` — textos exatos:
  - regra: `"[evento] O usuário confirmou o cartão #{id}: regra '{name}' criada (#{created_rule_id})."`
  - preferência: `"[evento] O usuário confirmou o cartão #{id}: preferência salva."`
  - ações aprovadas: `"[evento] O usuário aprovou {n} sugestão(ões) do cartão #{id} ({kind}); a execução foi enfileirada."` / recusadas: `"... recusou {n} ..."`
  - promoção/política: `"[evento] O usuário confirmou o cartão #{id}: {resumo}."`
  - falha: `"[evento] O cartão #{id} falhou: {error}."`
  `ChatStore.start_event_turn(conversation_id: int, text: str) -> TurnView | None`: `None` quando ocupada (`_busy`); senão grava `role='user', input='event', status='done'` + placeholder. `ChatStore.card_conversation(card_id: int) -> int | None`.
  Rota `confirm`: depois de `cards.confirm`, se `status in ("confirmed","failed")`, cria o evento e enfileira `ChatTurnJob`; devolve o `CardView` de sempre. `cancel` não continua.

- [ ] **Step 1: Testes que falham**
  - `test_card_event_texts` (um caso por tipo, strings exatas acima).
  - `test_confirm_enqueues_continuation`: confirmar cartão de regra → nova mensagem `input='event'` + `chat_turn` na fila.
  - `test_confirm_when_busy_skips_continuation` (Review Focus 3).
  - `test_cancel_does_not_continue`.
  - web: `confirm invalidates messages and renders event note`.
- [ ] **Step 2:** `pytest tests/test_chat_store.py tests/test_api_chat.py -v`; `cd web && npm test` → FAIL.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4:** PASS; `mypy src tests`; `cd web && npm test && npm run build`.
- [ ] **Step 5: Commit** `feat(chat): continue the conversation after a card is confirmed`

### Task 6: Prompt do chat v2, erros acionáveis e checagem do remetente da regra

**Files:**
- Create: `alembic/versions/0016_chat_prompt_v2.py`
- Modify: `src/agent/prompts.py` (`CHAT_PROMPT_V2`), `src/agent/tools/chat_cards.py` (`propose_rule`), `src/agent/ports.py` (`RuleReader.known_senders`), `src/agent/rules.py`
- Test: `tests/test_migrations.py`, `tests/test_chat_tools.py`, `tests/test_agent_tools.py`

**Interfaces:**
- Produces: `CHAT_PROMPT_V2` = V1 com a lista de tools atualizada (`propose_email_actions` no lugar de `propose_email_action`) e estas regras novas, literalmente:
  ```
  - Pedido sobre e-mails específicos ("apaga esses", "arquiva aqueles"): use
    propose_email_actions com os ids que você já citou ou com uma busca do Gmail.
    Regra (propose_rule) é só para o que deve acontecer daqui pra frente; nunca a
    ofereça no lugar de uma ação pedida agora.
  - Se uma tool falhar, diga em uma frase o erro que ela devolveu. Não invente
    causa e não mande o usuário fazer no painel o que uma tool sua faz.
  - "Sim", "mostre", "pode" respondem à sua última oferta: execute-a, sem nova
    pergunta.
  - Copie endereços, ids e assuntos exatamente como as tools devolveram.
  - Mensagens que começam com [evento] contam o que o usuário fez num cartão.
    Se o pedido dele ainda não terminou, continue; senão responda em uma frase.
  ```
  Migração 0016: cópia congelada do texto; insere `agent_configs` versão `max+1` copiando `decide_prompt`/`max_turns` da ativa, `chat_prompt=V2`, `active=true`, desativa as outras; downgrade remove a versão e reativa a anterior.
  `RuleReader.known_senders(address: str) -> tuple[int, tuple[str, ...]]`: (e-mails do endereço exato, até 3 endereços parecidos por `difflib.get_close_matches` com cutoff 0.8 sobre os remetentes do banco). `propose_rule` com `sender_address` e contagem 0 → `ValueError("nenhum e-mail de <addr>; confira o endereço" + "; parecido: <a>" quando houver)`.
- Mensagens de erro reescritas: `"e-mail não encontrado"` → `"esse e-mail não existe mais no Gmail"` (único caso restante após Task 2).

- [ ] **Step 1: Testes que falham**
  - `test_migration_0016_activates_v2_and_rolls_back`.
  - `test_propose_rule_unknown_sender_suggests_close_match`: banco com `googlealerts-noreply@google.com`, regra com `google-alerts-noreply@google.com` → erro contém `"parecido: googlealerts-noreply@google.com"`.
  - `test_chat_prompt_v2_mentions_batch_tool_only`: `"propose_email_actions" in CHAT_PROMPT_V2 and "propose_email_action(" not in CHAT_PROMPT_V2`.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4:** PASS; `mypy src tests`.
- [ ] **Step 5: Commit** `feat(chat): prompt v2, actionable tool errors, rule sender check`

### Task 7: Cadeia de modelos do chat

**Files:**
- Modify: `src/llm/gateway.py` (`_attempts`, `_backoff`, conversão de assinaturas), `src/llm/types.py` (`ChatMessage.signatures_model`), `src/agent/loop.py` (preenche `signatures_model=turn.model`), `src/llm/config.py` (`load_chat_config`, `build_chat_gateway`), `src/agent/wiring.py` (ChatTurnFlow usa o gateway do chat em `loop` e `structured`), `src/agent/flows/chat_turn.py` (`_reason` para esgotado), `.env.example`
- Test: `tests/test_llm_gateway.py`, `tests/test_chat_turn.py`

**Interfaces:**
- Produces:
  - `DEFAULT_CHAT_MODELS = "gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite"`; `build_chat_gateway(recorder: CallRecorder | None = None) -> LLMGateway` lê `CHAT_MODELS`.
  - `LLMGateway(..., wall: Callable[[], datetime] = lambda: datetime.now(UTC))`; estado `_exhausted: dict[str, datetime]` (até quando). `_QuotaGone` em um modelo → marca até a próxima meia-noite `America/Los_Angeles` e segue para o próximo modelo **sem dormir**; rotação pula modelos esgotados; todos esgotados → `LLMExhaustedError("cota do dia esgotada em todos os modelos", kinds=("quota",))`.
  - 503/429 curto: se o próximo modelo da rotação é outro, segue sem dormir; só aplica o backoff atual quando a próxima tentativa volta ao mesmo modelo.
  - `ChatMessage.signatures_model: str | None = None` (só `ai`); na conversão para LangChain, se `signatures_model` não for o modelo da tentativa, cada assinatura vira `"skip_thought_signature_validator"`.
  - `chat_turn._reason`: `kinds == ("quota",)` → `"A cota de hoje acabou em todos os modelos do chat; volta às 05h."`
  - `ErrorKind` ganha `"quota"`; `quota_exhausted()` (indexer) passa a aceitar `("quota",)` além do formato atual.

- [ ] **Step 1: Testes que falham**
  - `test_quota_gone_marks_model_and_moves_on`: modelo A levanta 429 com retry de 3600 s, B responde → resultado de B, `sleep` não chamado; chamada seguinte começa em B.
  - `test_exhausted_resets_at_pacific_midnight`: `wall` avança para 08:01 UTC do dia seguinte (inverno) → A volta.
  - `test_all_exhausted_fails_fast` (Review Focus 5).
  - `test_transient_switches_model_without_sleep`.
  - `test_foreign_signatures_replaced`: mensagem `ai` com `signatures_model="A"` enviada para B → assinatura `"skip_thought_signature_validator"`.
  - `test_chat_turn_quota_reason`.
- [ ] **Step 2:** `pytest tests/test_llm_gateway.py tests/test_chat_turn.py -v` → FAIL.
- [ ] **Step 3: Implementar.** Ajustar testes antigos do gateway que assumiam `sleep` entre modelos diferentes.
- [ ] **Step 4:** PASS; suíte inteira `pytest`; `mypy src tests`.
- [ ] **Step 5: Commit** `feat(llm): chat model chain with daily-quota memory`

### Task 8: Regressão da conversa de 2026-11-19

**Files:**
- Test: `tests/test_chat_regression.py`

**Interfaces:**
- Consumes: tudo acima; `FakeToolCaller` de `tests/agent_fakes.py`, banco SQLite de teste.

- [ ] **Step 1: Escrever** `test_spam_cleanup_conversation`: roteiro do `FakeToolCaller` igual aos runs 25–33 (`list_emails in:spam` → `show_emails` dos ids de spam → `propose_email_actions(trash)` com 5 ids) contra `ChatTurnFlow` real com mail fake de spam. Asserts: cartão de e-mails criado; um cartão de ações com 5 itens; histórico do 3º turno contém `"[e-mails desta resposta:"`; confirmar o cartão de ações cria mensagem `input='event'` e um `chat_turn` na fila.
- [ ] **Step 2:** `pytest tests/test_chat_regression.py -v` → PASS (se falhar, corrigir na task dona).
- [ ] **Step 3: Commit** `test(chat): regression for the spam cleanup conversation`

### Task 9: Entrega

- [ ] **Step 1:** `pytest`, `mypy src tests`, `cd web && npm test && npm run build` — tudo verde; colar a contagem no PR.
- [ ] **Step 2:** PR para `main` (push via helper do gh), descrição com o diagnóstico (runs 25–33) e os desvios.
- [ ] **Step 3:** depois do merge e deploy: no painel, repetir a conversa (ver spam → apagar os Alertas do Google → confirmar) e conferir em `agent_steps` que nenhuma tool falhou e que a continuação rodou; conferir em `llm_calls` qual modelo da cadeia respondeu.
