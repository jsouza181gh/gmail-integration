# Fase 4 (chaves e fila): desvios do contrato

1. **Sem chave: o job espera, não falha (jobs de fundo).** O pedido dizia "job falha com
   erro claro", a spec (seção "LLM com a chave do usuário") diz "ficam esperando a chave
   (não falham, não gastam tentativas)". Segui a spec: `NoLLMKeyError` faz o `Worker`
   devolver o job à fila por 15 min sem gastar tentativa, com o texto para leigo em
   `jobs.last_error`; nada vai a dead-letter nem alerta o admin. O `chat_turn` é a exceção:
   o turno falha na hora com a mesma mensagem (o usuário está olhando). `POST /api/backfill/start`
   responde 409 com o texto.
2. **Passo 5 não grava `users.onboarding_step`.** A coluna é da fase 3 (migração 0023).
   `src/api/routes/llm_key.py` acrescenta `"llm_key"` ao fim de `ONBOARDING_STEPS` e devolve
   `next_step`; o integrador liga o avanço do passo (ver to-dos).
3. **Rotas em `src/api/routes/llm_key.py`**, não em `onboarding.py`, para não colidir com a
   fase 3. Prefixo `/api/onboarding/llm-key` (GET status, POST valida e salva).
4. **Migração**: o diretório real é `alembic/versions/` (o contrato diz `migrations/`);
   `0024` aponta para `0021` neste branch.
5. **Fila justa**: a prioridade continua mandando (o maior nível de prioridade pronto vence);
   dentro dele, as contas se revezam (a servida há mais tempo na faixa vai primeiro). A
   memória do rodízio fica em processo (um worker por banco); `SKIP LOCKED` continua
   garantindo que não há claim duplo.
6. **Espera sem prender a faixa**: `LLMGateway(max_wait_s=15)`; esperas maiores (ritmo do
   free tier ou backoff de 429) levantam `LLMWaitError` (subclasse de `LLMExhaustedError`,
   `kinds=("rate",)`) e o `Worker` faz `defer(seconds)`. Esperas curtas continuam inline.
   CLIs usam `inline_wait_s=None` (comportamento antigo).
7. **Fallback para o `.env`**: só a conta do primeiro admin, só quando ela não tem linha em
   `user_llm_keys`, logado uma vez por processo. Qualquer outra conta sem chave não faz
   chamada alguma.
