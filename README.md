# gmail-integration

Classifica e-mails da INBOX automaticamente assim que chegam.

```
Gmail users.watch → Pub/Sub (push) → POST /webhooks/gmail (FastAPI)
  → sync (history.list desde o cursor salvo) → classificação (Gemini) → Postgres
```

```
src/
  api/app.py                  endpoint do webhook, /healthz, renovação diária do watch
  services/sync.py            Gmail history → tabela emails → classificação
  gmail/                      client (OAuth), authorize, watch, messages (resumo)
  classification/classifier.py  LLM (Gemini): lotes, retry, fallback de modelo
  db/                         session, models (SQLAlchemy), store (persistência)
  cli/                        classify_unread + main (classifica não lidos sob demanda)
  agent/tools.py              tools LangChain legadas (sem uso no fluxo atual)
  utils/console.py            log
alembic/                      migrações
tests/                        pytest
```

## Rodar

```bash
cp .env.example .env        # preencha GOOGLE_API_KEY, POSTGRES_PASSWORD, GMAIL_PUBSUB_TOPIC
python -m src.gmail.authorize   # local: gera secrets/token.json (abre o navegador)
docker compose up -d --build
curl http://127.0.0.1:8000/healthz
```

Sobe `db` (Postgres, sem porta publicada), `migrate` (`alembic upgrade head`) e `api` (em `127.0.0.1:8000`).
Se `secrets/token.json` não existir, a API sobe mesmo assim e loga o erro do watch.

> Se o dono de `secrets/` no host não for UID 1000, defina `APP_UID`/`APP_GID` no `.env` (`id -u`, `id -g`); senão a API não lê o `token.json`.

> A API roda com **1 worker** de propósito: o lock do sync e o agendador de renovação vivem no processo.

## Deploy na VPS

```bash
# da sua máquina
scp -r . ubuntu@<SEU_DOMINIO>:~/gmail-integration     # sem .venv/.git
scp secrets/token.json ubuntu@<SEU_DOMINIO>:~/gmail-integration/secrets/
ssh ubuntu@<SEU_DOMINIO>
cd ~/gmail-integration && docker compose up -d --build
docker compose logs -f api
```

Nginx do host (dentro do `server` HTTPS existente):

```nginx
location /webhooks/gmail {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

Depois: `sudo nginx -t && sudo systemctl reload nginx`. O endpoint da assinatura Pub/Sub deve ser
`https://<SEU_DOMINIO>/webhooks/gmail`.

## Proteger o endpoint (recomendado)

Sem `WEBHOOK_TOKEN` qualquer um pode chamar o webhook (o estrago é limitado a disparar syncs/chamadas ao LLM). Para ligar:

1. Defina `WEBHOOK_TOKEN=<valor longo aleatório>` no `.env` da VPS e `docker compose up -d`.
2. Troque o endpoint da assinatura para `https://<SEU_DOMINIO>/webhooks/gmail?token=<valor>`.

## Classificação

- **Taxonomia no banco, versionada:** `category_sets` → `categories` (+ `category_examples`) e `category_flags`. A v1 (11 categorias + flags `is_relevant`, `deadline`, `has_document`, `ephemeral`) entra pela migração `0003`. O prompt é montado da versão ativa a cada lote, então mudar a taxonomia não exige restart.
- **Preferências** (ex.: quais vagas interessam) ficam em `kb_entries` (`scope = classification`), nunca no prompt fixo.
- **Gateway LLM** (`src/llm/`): toda chamada passa por ele. Modo `LLM_MODE=free|fast`, retry/backoff, fallback entre `GEMINI_MODELS`, e cada tentativa vira uma linha em `llm_calls` (+ `llm_call_messages`, `llm_call_emails`).
- **Reclassificar** o que já está no banco com a taxonomia ativa:

```bash
docker compose run --rm api python -m src.cli.reclassify          # só e-mails de versões antigas
docker compose run --rm api python -m src.cli.reclassify --all    # todos
```

## Tipos

`mypy` estrito (sem `Any` explícito) cobre os módulos listados em `mypy.ini`:

```bash
.venv\Scripts\python -m mypy
```

## CLI

```bash
docker compose run --rm api python -m src.cli.main            # classifica não lidos
docker compose run --rm api python -m src.cli.main --reset
```

## Testes

```bash
.venv\Scripts\python -m pytest
```

Ao mudar dependências, atualize `requirements.txt` (a imagem instala só dele).
