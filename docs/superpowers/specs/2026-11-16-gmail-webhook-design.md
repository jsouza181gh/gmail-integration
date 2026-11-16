# Gmail webhook + Postgres + Docker — design

Data: 2026-11-16. Status: aguardando aprovação do João.

Legenda: **[decidido]** = dito pelo João. **[minha escolha]** = decisão do redator, o João pode trocar.

## Intenção

Hoje o projeto classifica e-mails não lidos sob demanda (`python main.py`), guardando em SQLite. O João quer que cada e-mail novo na INBOX seja classificado e salvo **automaticamente, assim que chegar**, sem rodar nada à mão. Para isso: Gmail `users.watch` → Pub/Sub push → endpoint FastAPI → fluxo de classificação existente → Postgres. Tudo sobe com um comando em uma VPS Oracle.

## Metas

- `POST /webhooks/gmail` recebe a notificação do Pub/Sub **[decidido]** (FastAPI **[decidido]**).
- Cada e-mail novo da INBOX é buscado, classificado (reusando `classify.py`) e salvo **[decidido]**.
- SQLite → Postgres via SQLAlchemy + Alembic **[decidido]**.
- `Dockerfile` + `docker-compose.yml`; `docker compose up -d` sobe tudo **[decidido]**.
- `watch` do Gmail renovado todo dia (expira em 7 dias) **[decidido]**; `historyId` persistido no banco **[decidido]**.
- Deploy via SSH em `ubuntu@<SEU_DOMINIO>`, nginx no host já com HTTPS **[decidido]**.

## Não-metas

- Criar tópico/assinatura Pub/Sub e dar permissão a `gmail-api-push@system.gserviceaccount.com`: já feito pelo João **[decidido]**.
- Editar o nginx da VPS ou executar qualquer coisa na VPS: o João faz; o design entrega o snippet e os comandos **[minha escolha]**.
- Migrar dados do `emails.db` antigo **[minha escolha]**: banco novo começa vazio. O CLI `main.py` continua funcionando, agora sobre Postgres.
- Várias contas Gmail, UI, ações automáticas (arquivar/responder/apagar).

## Anti-metas (falha mesmo "funcionando")

- Perder e-mails por falha transitória (LLM 429, Gmail 5xx, reinício do container).
- Classificar o mesmo e-mail duas vezes (Pub/Sub entrega *at-least-once*, notificações duplicadas e fora de ordem são normais).
- Estourar quota do Gemini por rajada de notificações.
- Pub/Sub reenviando em loop porque o endpoint demorou ou deu 5xx.

## Restrições

- Python 3.12 (o `.venv` atual). Código plano na raiz, no estilo atual.
- `GOOGLE_API_KEY` no `.env`; OAuth em `secrets/token.json` (gerado localmente por `authorize.py`; o João copia para a VPS com `scp`).
- Sem autenticação na assinatura push hoje **[decidido]**.
- `requirements.txt` está **incompleto** (falta langchain, langchain-google-genai, python-dotenv, pydantic); o builder deve regenerar a partir do `.venv` e adicionar fastapi, uvicorn, sqlalchemy, alembic, psycopg.

## Arquitetura

### Fluxo do webhook

1. Pub/Sub faz `POST /webhooks/gmail` com `{"message":{"data":"<base64 {emailAddress, historyId}>","messageId":...},"subscription":...}`.
2. Handler valida o token (se `WEBHOOK_TOKEN` definido), responde `204` imediatamente e agenda o sync em background. Payload malformado → `204` mesmo assim (log + descarta; evita retry infinito) **[minha escolha]**.
3. Sync (função síncrona em threadpool, protegido por um `Lock` para rodar uma vez por vez; notificações que chegam durante um sync apenas marcam "rodar de novo") **[minha escolha]**:
   - lê `history_id` salvo → `users.history.list(startHistoryId, historyTypes=["messageAdded"], labelId="INBOX")`, paginando;
   - o `historyId` **do payload é ignorado** para decidir o ponto de partida; vale o salvo (torna notificações forjadas/duplicadas inofensivas);
   - para cada mensagem nova: resumo (mesmo `_summary` de `classify.py`) → `INSERT ... ON CONFLICT DO NOTHING` em `emails` (idempotência por id do Gmail);
   - classifica os pendentes (`category IS NULL`) com `Classifier` existente, em lotes;
   - só depois grava o novo `history_id` (maior valor visto na resposta `history.list`/`historyId` da resposta). Crash antes disso → reprocessa, idempotente.
4. `history.list` retornando 404 (historyId expirado, >~1 semana sem sync) → fallback: listar `in:inbox newer_than:7d`, ingerir os desconhecidos, e reiniciar o cursor com o `historyId` atual do perfil (`users.getProfile`) **[minha escolha]**.
5. Pendentes que falharam (LLM fora) ficam com `attempts`/`last_error` e são retentados na próxima notificação e na rotina diária. Regra de desistência existente (`give_up`) mantida.

### Watch

- `watch.py` com `start_gmail_watch()` (do snippet do João: `labelIds=["INBOX"]`, `labelFilterBehavior="INCLUDE"`, `topicName` vindo de env `GMAIL_PUBSUB_TOPIC`, formato `projects/<id>/topics/<nome>`).
- Tabela `watch_state` (linha única): `email_address`, `history_id`, `expiration`, `updated_at`.
- No startup da API: se não há estado → chama watch e grava `history_id`. Depois, tarefa asyncio no `lifespan` renova a cada 24 h **[decidido: diário]**. Falha de renovação → log de erro e nova tentativa em 1 h.
- Na renovação diária também roda um sync (rede de segurança contra notificações perdidas).
- A API roda com **1 worker** uvicorn (scheduler e lock são em processo). Documentar.

### Banco

- SQLAlchemy 2.x (estilo declarativo), driver `psycopg` (v3), `DATABASE_URL` por env.
- Models: `Run`, `Email`, `RunEmail` (espelham as tabelas atuais) + `WatchState`. E-mails do webhook usam uma `Run` permanente com `query="webhook:inbox"` **[minha escolha]**, reaproveitando `pending()`/`save_classifications()`.
- `store.py` reescrito sobre SQLAlchemy mantendo a interface pública atual (`Store.get_or_create_run`, `save_page`, `pending`, `save_classifications`, `bump_attempts`, `give_up`, `report_rows`, `known_ids`, `count_pending`, `get_run`, `reset_cursor`) para `classify.py`/`main.py` mudarem o mínimo. Linhas retornadas devem continuar indexáveis por nome (`row["id"]`) ou os call-sites são ajustados.
- Alembic: `alembic/` na raiz, migração inicial cria as 4 tabelas, `env.py` lê `DATABASE_URL`.

### Docker

- `Dockerfile`: `python:3.12-slim`, usuário não-root, instala `requirements.txt`, copia o código.
- `docker-compose.yml`, serviços:
  - `db`: `postgres:16`, volume nomeado, healthcheck `pg_isready`, **sem porta publicada**.
  - `migrate`: mesma imagem, `alembic upgrade head`, `depends_on: db (healthy)`, roda e sai.
  - `api`: `uvicorn api:app --host 0.0.0.0 --port 8000 --workers 1`, `depends_on: migrate (service_completed_successfully)`, `restart: unless-stopped`, publica `127.0.0.1:8000:8000` (só o nginx do host alcança), `env_file: .env`, volume `./secrets:/app/secrets` (leitura e escrita: o refresh do token regrava `token.json`).
- `.env` ganha: `POSTGRES_PASSWORD`, `DATABASE_URL` montada no compose, `GMAIL_PUBSUB_TOPIC`, `WEBHOOK_TOKEN` (opcional), além das atuais. Criar `.env.example` sem segredos. Criar `.dockerignore` (`.venv`, `secrets`, `.env`, `emails.db*`, `__pycache__`).
- Snippet nginx a entregar (o João aplica):

```nginx
location /webhooks/gmail {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

### Segurança

- `WEBHOOK_TOKEN` opcional **[minha escolha]**: definido → exige `?token=` com comparação em tempo constante; ausente → aceita tudo (estado atual do João). README recomenda ligar e atualizar o endpoint da assinatura.
- `secrets/`, `.env`, `credentials.json` nunca entram na imagem.
- `GET /healthz` simples para checagem (nginx/compose) **[minha escolha]**.

### Estrutura de arquivos (proposta)

`api.py` (FastAPI, rotas, lifespan), `sync.py` (history → ingest → classify), `watch.py`, `db.py` (engine/session), `models.py`, `store.py` (reescrito), `alembic/`, `alembic.ini`, `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.env.example`, `README.md` (deploy: `scp secrets/token.json`, `docker compose up -d --build`, snippet nginx). `main.py` (CLI) permanece; o nome `api.py` evita conflito.

## Deixado ao builder

Nomes internos, formato dos logs, tamanho dos lotes (`CLASSIFY_BATCH_SIZE` existente), testes (pytest com Postgres via testcontainers ou SQLite só para a lógica pura, escolha do builder), detalhes do pool de conexões, política exata de retry da renovação.

## Critérios de pronto

- `docker compose up -d --build` local sobe `db`, `migrate`, `api` sem erro; `GET /healthz` = 200.
- `POST /webhooks/gmail` com payload de exemplo → 204; com `WEBHOOK_TOKEN` setado e token errado → 401/403.
- Sync com Gmail/LLM mockados: e-mail novo vira linha classificada; mesma notificação duas vezes não duplica; falha do LLM deixa pendente e a próxima rodada conclui; 404 de history cai no fallback.
- `alembic upgrade head` em banco vazio cria o schema; `alembic downgrade base` desfaz.
- `python main.py` (CLI) segue funcionando sobre Postgres.
