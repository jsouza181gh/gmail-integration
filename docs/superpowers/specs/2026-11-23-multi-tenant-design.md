# Plataforma multi-usuário — design

Data: 2026-11-23. Complementa `2026-11-19-gmail-assistant-design.md` (que
continua valendo para tudo que não está aqui) e
`2026-11-21-two-api-keys-design.md`.

## Intenção

Hoje o assistente atende uma pessoa (o João): um Gmail, um `token.json`, uma
chave do Gemini no `.env`, um usuário criado por CLI. A ideia é virar produto:
**qualquer pessoa convidada entra, clica em "Entrar com Google" e passa a usar
o assistente com o próprio Gmail** — e-mails novos chegando pelo webhook,
backfill opcional do histórico e chat sobre os próprios e-mails — sem nunca
tocar em Google Cloud.

Primeiro público: amigos próximos do João, por convite. Depois de validar,
cadastro aberto.

## Objetivos

1. Um amigo leigo, com o convite, completa a entrada **sozinho**, sem chamar o
   João, e termina com Gmail conectado, 2FA ativo e chave do Gemini validada.
2. Cada usuário vê **só os próprios dados**. Nenhuma tela ou API mostra dado de
   outro — nem para o João como admin.
3. Tudo que o João tem hoje (triagem, ações aprovadas, regras, KB, backfill,
   chat com lotes, áudio, documentos) funciona igual para cada usuário.
4. O backfill (ou qualquer carga) de um usuário não atrasa o chat nem a triagem
   de e-mail novo de outro.
5. A conta do João vira o usuário nº 1 com todos os dados de produção
   preservados, sem reprocessar nada.

## Anti-objetivo

**O fluxo ficar confuso para um usuário leigo.** Mesmo funcionando, se um
amigo travar no onboarding, falhou. Os três pontos de atrito conhecidos
(aviso "app não verificado" do Google, chave do Gemini, app autenticador)
têm tratamento obrigatório na seção Onboarding.

## Fora do escopo (agora)

- Mais de um Gmail por usuário — o modelo de dados já separa usuário de conta
  Gmail (1:1 por trava); a UX de várias contas fica para depois.
- Cadastro aberto — a checagem de convite é um ponto único, fácil de desligar
  depois.
- Outros provedores de LLM (só Gemini, chave do usuário).
- Verificação OAuth do Google / CASA, cobrança, planos.
- UI para o usuário trazer o próprio projeto GCP (BYO-GCP); só o modelo de
  dados fica pronto (ver `oauth_clients`).

## Decisões do João

1. **App OAuth único da plataforma**, no projeto GCP atual, publicado "In
   production" sem verificação (aviso de app não verificado; teto de 100
   usuários). O usuário nunca configura GCP.
2. **Entrada só por convite**: o João cadastra o e-mail; só esse e-mail entra.
3. **Login: Google ou senha, e TOTP sempre.** Senha é opcional, alternativa ao
   Google; o 2FA é obrigatório para todos.
4. **Isolamento total**: o admin (João) convida e vê saúde operacional, nunca
   conteúdo.
5. **Um Gmail por pessoa** por enquanto.
6. **Chave própria (BYOK), só Gemini**: o usuário cadastra a chave; o custo de
   IA é dele. (Inferido do PR #12 e do "chave/chaves" do João: uma chave
   obrigatória, segunda opcional para o backfill — ver Minhas escolhas.)
7. **Configuração inicial = cópia da parte genérica do João**: taxonomia,
   `agent_configs`, `tool_policies`. Regras de automação, KB, labels e
   histórico começam vazios (contêm dados pessoais do João).
8. **Saída**: desconectar/revogar guarda os dados; **excluir conta apaga tudo
   na hora**.
9. **Conta do João = usuário nº 1**, dados migrados, login Google ou senha com
   o TOTP atual.
10. **Ordem**: multi-tenant antes de qualquer outra evolução do painel; o
    painel é adaptado já pensando em vários usuários.

## Minhas escolhas (o builder pode rever; o João pode vetar)

- Dados pendurados na **conta Gmail** (`gmail_account_id`), não no usuário.
- Isolamento em duas camadas: filtro explícito por conta + **Row-Level
  Security** do Postgres como rede de segurança.
- Chave primária de `emails` vira `(gmail_account_id, id)`; FKs para e-mail
  viram compostas — referência cruzada entre contas fica impossível no banco.
- A cópia da configuração genérica é uma **foto no cadastro**; mudanças
  posteriores do João não propagam.
- **Primeiro acesso = um clique só no Google** (identidade + Gmail no mesmo
  consentimento). Login do dia a dia pede só `openid email`.
- Convite entregue por **link que o João copia** (WhatsApp etc.); a plataforma
  não envia e-mail de convite.
- Segunda chave Gemini opcional reaproveita faixas/turbo do PR #12.
- `global_lock` vira **bloqueio por usuário-alvo**; bloqueio por IP continua
  global.
- Admin pode **resetar o 2FA** de um usuário (força novo cadastro de TOTP no
  próximo login via Google); não expõe conteúdo.
- Excluir conta não apaga o convite; voltar exige convite novo.
- Modelo de embedding continua fixo na plataforma (`gemini-embedding-2`):
  trocar de **chave** não exige reindexar.

## Modelo de dados

Regras do projeto valem: tudo tipado em Pydantic, sem `Any`, **sem colunas
JSON/JSONB**.

### Tabelas novas / alteradas (plataforma)

- `users` (existe): + `email` (único, minúsculo), `google_sub` (único,
  nulo até o primeiro login Google), `role` (`admin` | `member`),
  `password_hash` passa a aceitar nulo, `totp_secret_enc` aceita nulo durante
  o onboarding, `onboarding_step`.
- `invites`: `email`, `created_by`, `created_at`, `accepted_at`,
  `revoked_at`.
- `oauth_clients`: `id`, `kind` (`platform` hoje; `byo` no futuro),
  `client_id`, `client_secret_enc`. Uma linha agora.
- `gmail_accounts`: `id`, `user_id` (**único** — a trava do 1:1),
  `email_address` (único, minúsculo), `oauth_client_id`,
  `refresh_token_enc`, `scopes`, `status` (`connected` | `disconnected` |
  `revoked`), `history_id`, `watch_expiration`, `connected_at`,
  `last_sync_at`, `last_error`. **Substitui `watch_state` e
  `secrets/token.json`.**
- `user_llm_keys`: `user_id`, `app_key_enc`, `backfill_key_enc` (nulo),
  `validated_at`, `last_error`.
- `account_settings`: colunas tipadas por conta para o que hoje está em
  `app_settings` e é por conta (`llm_mode`, `backfill_paused`,
  `backfill_turbo`). `app_settings` fica só para chaves da plataforma.

Segredos (`*_enc`) cifrados com Fernet; usar `MultiFernet` para permitir
rotação.

### `gmail_account_id` em toda tabela de conteúdo

Coluna `NOT NULL`, **direta** (desnormalizada onde hoje se chega por FK),
para a política de RLS ser uma comparação simples:

`emails`, `runs`, `run_emails`, `email_embeddings`, `attachments`,
`category_sets` (+ `categories`, `category_flags`, `category_examples`),
`kb_entries`, `agent_configs`, `agent_runs`, `agent_steps`, `gmail_labels`,
`tool_policies`, `automation_rules`, `suggested_actions`, `feedback`,
`notifications`, `jobs`, `llm_calls`, `llm_call_messages`,
`llm_call_emails`, `config_audit`, `chat_conversations`, `chat_messages`,
`chat_message_refs`, `chat_audio`, `chat_cards`, `chat_card_items`,
`account_settings`.

Unicidades que hoje são globais passam a ser por conta: `category_sets.version`,
`agent_configs.version`, `tool_policies(target, trigger)`, `gmail_labels.name`,
`attachments(email_id, part_id)`, `jobs.idempotency_key`, índice parcial
`uq_suggested_actions_live`.

Tabelas de segurança continuam da plataforma, ligadas a `users`:
`auth_sessions`, `recovery_codes`, `login_failures`, `ip_blocks`,
`security_events` (+ `user_id` nulo).

### Row-Level Security

- A aplicação conecta com um papel **sem** `BYPASSRLS`; toda transação que
  serve um usuário ou um job define a conta (`SET LOCAL app.account_id`).
- Política em cada tabela de conteúdo: `gmail_account_id =
  current_setting('app.account_id')::int`. Sem conta definida → zero linhas.
- Caminhos de sistema que precisam ver todas as contas usam uma conexão
  separada e **listada**: claim de jobs, roteamento do webhook, renovação de
  watch, métricas do admin (só contagens/estados), exclusão de conta. Nenhum
  desses caminhos devolve conteúdo (assunto, remetente, corpo, texto de chat,
  prompt).
- Como implementar (papéis, `SET LOCAL` via evento de sessão, se `jobs` fica
  com RLS ou não) é do builder, respeitando a regra acima.

### Migração da conta do João

Uma migração Alembic: cria usuário/conta nº 1 a partir de `users` (único) e
`watch_state` (e-mail, `history_id`, expiração), marca `role=admin`, preenche
`gmail_account_id=1` em tudo, move `llm_mode`/`backfill_*` para
`account_settings`, cria a linha `platform` em `oauth_clients`. As chaves do
`.env` (`GOOGLE_API_KEY[_BACKFILL]`) vão para `user_llm_keys` do João por um
comando de CLI pós-deploy (a migração não lê `.env`).

O refresh token atual é do client **Desktop**; o fluxo web usa um client
**Web** novo. O João reconecta o Gmail uma vez depois do deploy; o
`history_id` guardado faz o sync recuperar o intervalo (o fallback de "últimos
dias da INBOX" já existe para `historyId` expirado).

## Google: OAuth e Gmail

### Setup manual (João, uma vez, no console)

Client OAuth **Web app** no projeto atual, redirect
`{APP_ORIGIN}/api/oauth/google/callback`; tela de consentimento com nome,
logo, política de privacidade simples; status **In production**.

**Risco a validar primeiro (fase 0):** confirmar na prática que um app não
verificado, em produção, com `gmail.modify` deixa uma conta de teste passar
pelo aviso e emite refresh token sem prazo de 7 dias. Se o Google bloquear, o
plano B é modo Testing com o amigo como test user — mas aí o refresh token
expira em 7 dias, o que quebra a promessa. Nesse caso, voltar ao João antes de
seguir.

Também na fase 0: confirmar que o domínio atual (`jsouza181.duckdns.org`)
é aceito como domínio autorizado da tela de consentimento e onde hospedar a
página de política de privacidade exigida.

### Fluxos

- **Primeiro acesso** (convite aberto): um consentimento com `openid email
  profile` + `gmail.modify` + `gmail.settings.basic`, `access_type=offline`,
  `prompt=consent`, PKCE e `state` assinado de vida curta. Callback: confere
  convite pelo e-mail verificado do Google, cria usuário + `gmail_accounts`,
  guarda refresh token cifrado, inicia o watch.
- **Login**: `openid email`, `prompt=select_account`; casa por `google_sub`
  (primeira vez, por e-mail). Sem conta e sem convite → tela "você ainda não
  foi convidado". Depois do Google → TOTP.
- **Login por senha**: e-mail + senha → TOTP (lógica atual).
- **Reconectar Gmail**: mesmo consentimento do primeiro acesso, só para quem
  já tem conta; o e-mail do Gmail precisa ser o mesmo da conta.

### Cliente Gmail por conta

`get_gmail_service()` vira uma fábrica por conta (`gmail_for(account_id) ->
Callable[[], Resource]`). Os consumidores já recebem uma função sem argumento,
então só a montagem muda. Refresh renovado é salvo de volta cifrado.
`invalid_grant` → conta `revoked`, watch para, aviso no painel ("reconecte o
Gmail"), alerta operacional sem conteúdo.

### Pub/Sub e watch

- Mesmo tópico e mesma assinatura push (OIDC continua).
- Webhook: usa o `emailAddress` do push → `gmail_accounts` → dispara o sync
  **daquela conta**. E-mail desconhecido ou conta não conectada → 204 e log.
- `SyncCoordinator` por conta (coalescência por conta).
- Renovação: um laço percorre contas `connected` e renova as que vencem em
  menos de ~2 dias. Falha em uma conta não afeta as outras.

## LLM com a chave do usuário

- Gateways por chave, em cache no processo (a memória de cota/pausa/ritmo
  continua sendo por chave, como no PR #12). Job → conta → usuário → chaves →
  gateways.
- **Sem chave válida**: e-mail novo continua sendo ingerido (sync não usa
  LLM); jobs que precisam de LLM ficam esperando a chave (não falham, não
  gastam tentativas); chat e backfill ficam bloqueados com aviso claro no
  painel.
- Ao salvar, a chave é validada com uma chamada barata; erro explicado em
  linguagem humana ("essa chave não existe", "essa chave não tem acesso ao
  Gemini", "cota do dia acabou").
- Busca e embeddings no modelo fixo da plataforma.

## Fila justa

As três faixas do worker continuam (chat, app, backfill). Restrições novas:

1. Dentro de uma faixa, contas se alternam: uma conta com 80 mil e-mails na
   fila não segura o `process_email` ou o `chat_turn` de outra.
2. **Espera de ritmo/cota de uma chave não pode prender a faixa**: hoje o
   gateway dorme dentro do job (free tier); com várias chaves isso faria a
   espera do usuário A atrasar o usuário B. O job precisa devolver-se à fila
   com `run_after` ou a faixa precisa de concorrência por conta — escolha do
   builder.
3. Pausa/turbo do backfill passam a ser por conta.

Meta mensurável: com um backfill grande rodando em uma conta, um e-mail novo
em outra conta é triado no mesmo tempo de hoje (± a latência do Gemini).

## Onboarding (o anti-objetivo mora aqui)

Telas em passos, uma coisa por tela, barra de progresso, texto sem jargão,
retomável (fechar o navegador e voltar cai no passo onde parou):

1. **Convite** (`/entrar`): o que é o assistente em 2 linhas; **preparo do
   aviso do Google**, com imagem da tela "O Google não verificou este app" e
   onde clicar ("Avançado" → "Acessar…"), explicando por quê.
2. **Google** (um clique; consentimento com Gmail).
3. **Proteção da conta (TOTP)**: QR, sugestão de app (Google Authenticator /
   Microsoft Authenticator) com link da loja, campo para o código.
4. **Códigos de recuperação**: copiar/baixar + caixa "guardei meus códigos".
5. **Chave do Gemini**: link direto para o AI Studio, 3-4 imagens curtas do
   caminho, campo, validação na hora ("chave funcionando ✓"). Segunda chave
   em "avançado", fechada por padrão.
6. **Pronto**: oferta do backfill com estimativa em palavras ("seus e-mails
   antigos vão aparecendo ao longo de alguns dias") e botão para começar ou
   pular.

Critério de aceite: alguém que nunca viu o produto completa os passos 1-6 sem
ajuda. Verificar com um amigo real antes de chamar mais gente.

## Painel

- **Configurações** (todo usuário): Conta (e-mail, senha opcional,
  sessões, 2FA); Gmail (status, reconectar, desconectar); Chaves de IA;
  Excluir conta (digitar o e-mail para confirmar; explica o prazo de
  retenção dos backups).
- **Avisos de estado** no topo quando faltar algo: sem chave, Gmail
  desconectado/revogado, backfill não iniciado.
- **Admin** (só `role=admin`): Convites (adicionar e-mail, copiar link,
  revogar); Contas (e-mail, status da conexão, último sync, vencimento do
  watch, fila por faixa, falhas recentes, chave válida sim/não). **Nenhum
  conteúdo.** Resetar 2FA. Bloqueios de IP.
- Páginas existentes passam a mostrar só a conta logada. "Segurança" vira por
  usuário (sessões e eventos dele); bloqueios de IP vão para o admin.
- Design system existente ("Inbox Agent"); telas novas seguem o mesmo kit.

## Alertas

- Operacionais (sync, watch, worker): para o admin, pelo Gmail do admin, citando
  só o e-mail da conta afetada e o tipo de erro.
- Segurança do usuário (login de IP novo, reuso de refresh): para o próprio
  usuário, pelo Gmail dele (`send_to_self` como hoje).
- Falhas seguidas do agente: aviso no painel do usuário + alerta operacional ao
  admin.

## Desconectar e excluir

- **Desconectar**: `users.stop`, revoga o token no Google, conta
  `disconnected`. Dados ficam. Reconectar retoma pelo `history_id` (ou
  fallback).
- **Revogado pelo Google**: igual, status `revoked`.
- **Excluir conta**: para o watch, revoga o token, apaga em uma transação
  todas as linhas da conta (conteúdo, configuração, chaves, sessões,
  códigos), depois o usuário. Os backups do Postgres guardam cópias até a
  rotação — o builder levanta o prazo atual em `deploy/` e ele aparece na
  tela de exclusão.

## CLI

Comandos passam a exigir `--account <email>`. `create_user` dá lugar a
`invite <email>` (e o primeiro admin é o João, via migração). Comando para
importar as chaves do `.env` para o João.

## Fases (cada uma vai para produção sozinha)

0. **Spike OAuth**: client Web, app em produção não verificado, conta de
   teste passa pelo aviso e recebe refresh token sem prazo de 7 dias.
1. **Base multi-conta**: tabelas novas, `gmail_account_id` em tudo, PK
   composta, RLS, migração do João. Comportamento visível igual ao de hoje.
2. **Gmail por conta**: fábrica de cliente, tokens no banco, webhook
   roteado, laço de watch, reconexão do João no client Web.
3. **Login e entrada**: Google login, convites, onboarding (passos 1-4),
   senha opcional, TOTP obrigatório, `global_lock` por usuário.
4. **Chaves e fila**: `user_llm_keys`, passo 5 do onboarding, gateways por
   chave, fila justa, espera sem prender a faixa.
5. **Painel**: configurações, avisos, admin, passo 6, páginas filtradas.
6. **Saída**: desconectar e excluir.

## Testes obrigatórios

- **Isolamento**: fixture com duas contas com dados; cada rota da API e cada
  tool do chat, autenticada como A, nunca devolve nada de B (inclusive por
  id adivinhado). Mais um teste com RLS ligado e filtro "esquecido" de
  propósito, provando que o banco barra.
- Migração do João: contagens antes/depois iguais em todas as tabelas.
- Webhook com `emailAddress` de A dispara só o sync de A.
- Fila: backfill grande em A não atrasa `process_email` de B (teste de
  ordem de claim).
- Exclusão: depois de excluir A, nenhuma tabela tem linha com a conta de A.

## Deixado para o builder

Mecânica de RLS e papéis do Postgres; algoritmo de rodízio da fila; cache e
expiração dos gateways; formato do `state` OAuth; texto final das telas
(respeitando o anti-objetivo); ordem interna das tarefas dentro de cada fase.
