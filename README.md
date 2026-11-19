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

## Deploy automático (GitHub Actions)

- **PR / push em branch:** `ci.yml` roda `pytest`, `mypy` e `alembic upgrade → downgrade → upgrade` num Postgres 16 real.
- **Merge na `main`:** `deploy.yml` roda o CI de novo, publica `ghcr.io/jsouza181gh/gmail-integration:<sha>` (+ `latest`) e entra na VPS por SSH com uma chave que só executa `deploy/deploy.sh`.
- **`deploy/deploy.sh <sha>`:** `pg_dump` para `backups/` (guarda 7) → `docker compose up -d` (roda `migrate` antes da `api`) → smoke test (`api` healthy + `/healthz` 200, até 120 s). Se falhar: **rollback automático** para a imagem anterior e o workflow termina vermelho — o GitHub manda o e-mail de falha.
- O `docker-compose.yml` usado em cada versão vem de dentro da própria imagem; a VPS só guarda `.env`, `secrets/`, `deploy/`, `backups/` e `.deploy/` (estado).

> **Migrações só adicionam** (tabela, coluna nula, índice). Remover/renomear é feito em dois deploys: primeiro o código para de usar, depois a migração remove. Assim o rollback para a imagem anterior sempre funciona com o schema novo. O rollback **não** desfaz migração; para isso existe o backup.

### Setup único na VPS

```bash
# 1. usuário dedicado, sem sudo, no grupo docker
sudo adduser --disabled-password --gecos "" deploy
sudo usermod -aG docker deploy

# 2. app em /home/deploy/gmail-integration (mesmo nome de projeto compose → mesmo volume do banco)
sudo -u deploy mkdir -p /home/deploy/gmail-integration/{deploy,secrets,backups}
sudo cp ~/gmail-integration/.env /home/deploy/gmail-integration/.env
sudo cp ~/gmail-integration/secrets/token.json /home/deploy/gmail-integration/secrets/
sudo chown -R deploy:deploy /home/deploy/gmail-integration
sudo chmod 600 /home/deploy/gmail-integration/.env /home/deploy/gmail-integration/secrets/token.json
# no .env: APP_UID/APP_GID = `id -u deploy` / `id -g deploy`
```

```bash
# 3. da sua máquina: o script de deploy (repetir só quando deploy/deploy.sh mudar)
scp deploy/deploy.sh ubuntu@<SEU_DOMINIO>:/tmp/ && ssh ubuntu@<SEU_DOMINIO> \
  'sudo install -o deploy -g deploy -m 755 /tmp/deploy.sh /home/deploy/gmail-integration/deploy/deploy.sh'

# 4. chave só para o CI (sem senha), restrita ao script
ssh-keygen -t ed25519 -N "" -C gha-deploy -f gha_deploy
# conteúdo de gha_deploy.pub vai para /home/deploy/.ssh/authorized_keys (dono deploy, 600), prefixado:
# command="/home/deploy/gmail-integration/deploy/deploy.sh",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding ssh-ed25519 AAAA... gha-deploy
```

5. **Secrets do repositório** (Settings → Secrets and variables → Actions): `DEPLOY_HOST` (domínio/IP), `DEPLOY_KEY` (conteúdo de `gha_deploy`, depois apague o arquivo local), `DEPLOY_KNOWN_HOSTS` (saída de `ssh-keyscan -t ed25519 <SEU_DOMINIO>`).
6. **Primeiro push da imagem:** depois do primeiro deploy, em *Packages → gmail-integration → Package settings*, mude a visibilidade para **Public** (o GHCR cria privado; a VPS faz pull sem login).
7. Pare o stack antigo antes do primeiro deploy: `cd ~/gmail-integration && docker compose down` (sem `-v`: o volume do banco fica).

Deploy manual, se precisar: `ssh -i gha_deploy deploy@<SEU_DOMINIO> deploy <sha-completo>`.

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
