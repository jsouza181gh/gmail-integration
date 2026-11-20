# gmail-integration

Assistente que desentulha a INBOX: cada e-mail novo passa por um agente que classifica, etiqueta e propõe ações (arquivar, lixeira, ...) para aprovação.

```
Gmail users.watch → Pub/Sub (push) → POST /webhooks/gmail (api)
  → sync (history.list desde o cursor salvo) → emails (Postgres) → job process_email
worker → agente: classifica → regras → labels IA/* → histórico do remetente → decide (Gemini + tools)
  → sugestões (suggested_actions) → aprovação (CLI) → job execute_action → Gmail
```

```
src/
  api/app.py                  endpoint do webhook, /healthz, renovação diária do watch
  services/sync.py            Gmail history → tabela emails → fila de jobs
  worker.py                   processo do agente (consome a fila)
  jobs/                       fila no Postgres (SKIP LOCKED, backoff, dead-letter) + loop do worker
  agent/                      runtime do agente: contexto/estado tipados, tools, loop, trilha, política
  agent/flows/                process_email (e-mail novo) e execute_action (ação aprovada)
  alerts/                     alertas por e-mail do servidor para o próprio endereço
  llm/                        gateway LLM único (LangChain + Gemini)
  gmail/                      client (OAuth), authorize, watch, messages, reader, labels, actions
  classification/             taxonomia, prompt e classificador em lote
  db/                         session, models (SQLAlchemy), rows, store
  cli/                        enqueue, actions (aprovação), rules, reclassify, classify_unread
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

Sobe `db` (Postgres, sem porta publicada), `migrate` (`alembic upgrade head`), `api` (em `127.0.0.1:8000`) e `worker` (agente).
Se `secrets/token.json` não existir, a API sobe mesmo assim e loga o erro do watch.

> Se o dono de `secrets/` no host não for UID 1000, defina `APP_UID`/`APP_GID` no `.env` (`id -u`, `id -g`); senão a API não lê o `token.json`.

> A API roda com **1 worker** de propósito: o lock do sync e o agendador de renovação vivem no processo.

## Deploy automático (GitHub Actions)

- **PR / push em branch:** `ci.yml` roda `pytest`, `mypy` e `alembic upgrade → downgrade → upgrade` num Postgres 16 real.
- **Merge na `main`:** `deploy.yml` roda o CI de novo, publica `ghcr.io/jsouza181gh/gmail-integration:<sha>` (+ `latest`) e entra na VPS por SSH com uma chave que só executa `deploy/deploy.sh`.
- **`deploy/deploy.sh <sha>`:** `pg_dump` para `backups/` (guarda 7) → `docker compose up -d` (roda `migrate` antes da `api`) → smoke test (`api` healthy + `/healthz` 200 e `worker` healthy, até 120 s). Se falhar: **rollback automático** para a imagem anterior e o workflow termina vermelho — o GitHub manda o e-mail de falha.
- O `docker-compose.yml` usado em cada versão vem de dentro da própria imagem; a VPS só guarda `.env`, `secrets/`, `deploy/`, `backups/` e `.deploy/` (estado).

> **Migrações só adicionam** (tabela, coluna nula, índice). Remover/renomear é feito em dois deploys: primeiro o código para de usar, depois a migração remove. Assim o rollback para a imagem anterior sempre funciona com o schema novo. O rollback **não** desfaz migração; para isso existe o backup.

### Setup único na VPS

Automatizado por `deploy/setup-vps.sh` (idempotente): `sudo bash setup-vps.sh <chave-pública-do-CI> <deploy.sh> [pasta-da-instalação-atual]`. Passo a passo equivalente:

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

## Agente

Fluxo de cada e-mail novo (`src/agent/flows/process_email.py`), tudo registrado em `agent_runs` → `agent_steps` (cada chamada LLM liga ao seu step em `llm_calls`):

1. **planejado** (o fluxo chama as tools, sem gastar decisão do modelo): `classify_email` → regras de automação → `apply_labels` (`IA/<categoria>`, `IA/Relevante`) → `sender_history`;
2. se uma regra casou, ela decide; senão um **loop curto** (até `max_turns` da `agent_configs` ativa) com as tools `get_email_body` e `propose_action`, vendo o e-mail (como dado), a classificação, o histórico do remetente e a KB.

Tools são `@tool` do LangChain com `runtime: ToolRuntime[<Fatia>Ctx, <Fatia>State]`: cada uma recebe só a fatia do contexto/estado de que precisa (`src/agent/tools/__init__.py`) e devolve um `StateUpdate` tipado em vez de mutar estado.

**Política:** labels `IA/*` automáticas; marcar lido, estrela, arquivar, lixeira e bloquear remetente viram sugestão (`tool_policies`, padrão `suggest`). `block_sender` nunca roda sozinho (invariante no código). Não existe exclusão permanente.

```bash
docker compose exec worker python -m src.cli.actions list                       # fila de aprovação
docker compose exec worker python -m src.cli.actions approve --sender loja.com  # em lote (pede confirmação)
docker compose exec worker python -m src.cli.actions reject --id 12 --comment "isso eu quero ver"  # vira KB
docker compose exec worker python -m src.cli.rules add --name lojas --domain loja.com --action trash
docker compose exec worker python -m src.cli.rules promote --id 1               # suggest → auto
docker compose exec worker python -m src.cli.enqueue --days 7                   # processa e-mails antigos
```

- **Bloquear remetente** exige o escopo `gmail.settings.basic`: rode `python -m src.gmail.authorize` de novo (agora pede os dois escopos) e recopie `secrets/token.json` para a VPS. Sem isso, a ação falha com aviso e nada mais quebra.
- **Alertas** por e-mail (`[gmail-integration] …`, label `Sistema/Alerta`, no máximo 1 por tipo a cada 15 min): job em dead-letter, 3 execuções do agente falhando seguidas, 3 syncs falhando seguidos, falha na renovação do watch, backfill concluído.
- Env: `WORKER_POLL_S` (5), `WORKER_STALE_MIN` (15), `JOB_MAX_ATTEMPTS` (5), `LABELS_DRY_RUN=1` (labels só no log).

## API do painel (autenticada)

Tudo sob `/api`, mesma origem do front (a própria API serve o painel em `/`; o nginx só repassa tudo para ela), sem CORS. A API só lê, registra decisões e enfileira jobs: não chama LLM, e do Gmail só lê (download de anexo, total da caixa).

- **Login:** senha → TOTP (ou recovery code). Usuário único, criado na VPS. Access JWT de 10 min e refresh de 14 dias (rotacionado a cada uso; reuso de um refresh antigo derruba a família inteira e manda alerta) em cookies `HttpOnly; Secure; SameSite=Strict`.
- **Bloqueios:** 3 falhas de um IP em 24 h → IP bloqueado; falhas de 3 IPs em 24 h → login fechado para todos. Só a CLI desbloqueia.
- **Superfície:** CSRF (cookie `csrf` + header `X-CSRF-Token`) e `Origin` em toda escrita; HSTS, CSP, `X-Frame-Options`; rate limit 60/min por rota (10/min no login); `/docs` desligado em produção.
- **Endpoints:** `auth/*`, `emails` (+ `relevant`, detalhe, `reclassify`), `categories`, `actions` (+ `approve`/`reject` em lote), `documents` (+ link de download de 60 s), `backfill` (progresso, %, ETA, pausa por cota) e `backfill/start`, `runs`, `jobs` (+ `requeue`), `usage`, `security/events`, `security/blocks`, `notifications`, `agent/*` (versões do prompt com rollback, políticas, regras, KB, `llm_mode`, auditoria).

Ligar na VPS:

1. No `.env`: `JWT_SECRET` (`python -c "import secrets;print(secrets.token_urlsafe(48))"`), `APP_SECRET` (`python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"`), `APP_ORIGIN=https://<SEU_DOMINIO>`, `APP_ENV=production`. Sem `JWT_SECRET` a API do painel fica desligada (webhook segue funcionando).
2. `docker compose up -d` e crie o usuário: `docker compose exec api python -m src.cli.create_user <usuario>` — escaneie o QR e guarde os 10 recovery codes.
3. Nginx (no mesmo `server` HTTPS do webhook):

```nginx
location /api/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

Desbloqueio: `docker compose exec api python -m src.cli.unlock --list | --ip <IP> | --global`.

**Webhook com OIDC (recomendado):** na assinatura push do Pub/Sub, marque *Enable authentication*, escolha uma service account e use como audience `https://<SEU_DOMINIO>/webhooks/gmail`. No `.env`: `PUBSUB_AUDIENCE` (a mesma URL) e `PUBSUB_SERVICE_ACCOUNT` (o e-mail da service account). Com os dois definidos, o `?token=` deixa de valer.

## Painel web

Vite + React + TypeScript em `web/`, fiel ao design "Inbox Agent" (uma cor só, brasa sobre quase-preto). Toda resposta da API passa por schema zod; `any` é proibido (oxlint). O build (`npm run build`) vai para a imagem Docker (`/app/web/dist`, variável `WEB_DIST`) e a API serve o painel em `/` com fallback de SPA; `/api`, `/webhooks` e `/healthz` continuam vindo antes.

```bash
cd web && npm ci
APP_ORIGIN=https://<SEU_DOMINIO> npm run dev   # proxy de /api para 127.0.0.1:8000, reescrevendo o Origin
npm test && npm run lint && npm run typecheck && npm run build
```

- Em dev a API precisa estar de pé (`uvicorn src.api.app:build_app --factory`) com `APP_ORIGIN` igual ao que o proxy envia; o cookie `Secure` funciona em `http://localhost`.
- Aprovar/recusar espera 5 s (botão "Desfazer", tecla `Z`) antes de ir para a API; sair da tela envia na hora.

Nginx do host, no mesmo `server` HTTPS (passo manual, depois do deploy; com ele `/api/` e `/webhooks/gmail` já cobertos acima):

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

## Backfill e documentos

Indexa o histórico inteiro (menos chats, rascunhos, spam e lixeira), do mais novo para o mais antigo: corpo em texto, classificação, vetor (`gemini-embedding-2`, `vector(1536)` no pgvector) e anexos. E-mails novos são indexados pelo job `index_email` que o sync enfileira.

- **Corrente de jobs `backfill_page`**: 25 mensagens por job, prioridade mínima (e-mails novos e ações sempre passam na frente). O cursor fica em `runs` e só avança no fim da página; refazer uma página não duplica nada.
- **Sobrevive a deploy:** ao subir, o worker devolve à fila todo job que estava `running`, sem contar tentativa (há um worker por banco). Um job de página que já ficou para trás do cursor reenfileira a página atual, então a corrente não quebra. `backfill start` pode ser repetido a qualquer momento.
- **Cota do free tier:** quando o Gemini pede para esperar mais de 2 min (cota diária), o job é adiado 30 min sem gastar tentativa.
- **Não gera sugestões** para e-mail antigo: sem regras, sem loop do agente, sem labels.
- **Anexos:** PDF com texto é lido localmente (pypdf); PDF escaneado e imagem vão como arquivo para o Gemini (OCR). Classes `pessoal` / `trabalho` / `fiscal` / `outro` (derivadas do tipo: RG, CNH, holerite, boleto, NF-e…), com emissor, valor, emissão, vencimento e linha digitável. O arquivo nunca é guardado: só metadados, texto e vetor; `get` baixa do Gmail na hora pelo `partId`.

```bash
docker compose exec worker python -m src.cli.backfill start      # cria/retoma (repara a corrente)
docker compose exec worker python -m src.cli.backfill status
docker compose exec worker python -m src.cli.jobs status          # fila por tipo
docker compose exec worker python -m src.cli.jobs dead            # jobs mortos
docker compose exec worker python -m src.cli.jobs requeue --all-dead --kind backfill_page
docker compose exec worker python -m src.cli.docs search "boleto de energia"
docker compose exec worker python -m src.cli.docs list --class fiscal
docker compose exec worker python -m src.cli.docs get 42 --out /tmp/doc.pdf
```

- Env: `EMBED_MODEL` (`gemini-embedding-2`; trocar exige revetorizar tudo).
- Banco: imagem `pgvector/pgvector:pg16` (mesmo formato de dados do Postgres 16).

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
