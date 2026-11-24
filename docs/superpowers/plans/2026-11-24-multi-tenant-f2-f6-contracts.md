# Multi-tenant fases 2–6 — contrato entre implementações paralelas

Spec: `docs/superpowers/specs/2026-11-23-multi-tenant-design.md` (fonte da verdade).
Fase 1 já em produção (`docs/superpowers/plans/2026-11-23-multi-tenant-f1-base.md`):
`src/db/scope.py` (`account_scope`, `system_scope`, `current_account`, `AccountOwned`),
`src/db/accounts.py`, RLS + role `gi_app`, `gmail_accounts`, `account_settings`.

As fases 2–6 são implementadas **em paralelo**, cada uma num branch próprio a partir
deste commit, e integradas **na ordem 2 → 3 → 4 → 5 → 6**. Para os merges serem
mecânicos, todas seguem este contrato. Uma fase pode **usar** um nome de outra fase
listado aqui mesmo que ele ainda não exista no seu branch: nesse caso cria um stub
mínimo com a assinatura exata abaixo, num arquivo próprio, marcado
`# STUB(fase N): substituído no merge`, e testa contra um fake.

## Regras do projeto (valem para todas)

- Tudo tipado em Pydantic; `Any` proibido (mypy strict + `disallow_any_explicit`).
- **Sem colunas JSON/JSONB.** Listas viram texto separado por espaço ou tabela.
- Toda tabela de conteúdo por conta herda `AccountOwned`; caminhos sem conta usam
  `system_scope()` e nunca devolvem conteúdo.
- Nada que esconda e-mail ou mude leitura roda sem aprovação; `messages.delete` proibido.
- Testes: `.venv/Scripts/python.exe -m pytest -q` (SQLite) verde; `mypy` verde;
  web: `npm --prefix web test` e `npm --prefix web run build` verdes quando mexer em `web/`.
- CI roda `alembic upgrade head && alembic downgrade base && alembic upgrade head`:
  toda migração tem downgrade. `tests/test_migrations.py` exige paridade modelo×migração.
- Commits pequenos, mensagem em inglês no estilo `feat(scope): ...`.
- Textos de UI em português, pensados para leigo (anti-objetivo da spec).

## Migrações reservadas (cadeia fixa)

| Fase | revision | down_revision | arquivo |
|---|---|---|---|
| 2 | `0022` | `0021` | `migrations/versions/0022_gmail_per_account.py` |
| 3 | `0023` | `0022` | `migrations/versions/0023_login_invites.py` |
| 4 | `0024` | `0023` | `migrations/versions/0024_llm_keys_fair_queue.py` |
| 5 | `0025` | `0024` | `migrations/versions/0025_panel_admin.py` (só se precisar) |
| 6 | `0026` | `0025` | `migrations/versions/0026_account_exit.py` (só se precisar) |

No seu branch, se a revisão anterior ainda não existir, aponte `down_revision` para a
última que existe (`0021`) e anote no commit — o integrador corrige no merge.
Confira o nome real do diretório de migrações no repo antes de criar.

## Fase 2 — Gmail por conta (produz)

Migração 0022:
- `oauth_clients`: `id` int PK, `kind` String(16) (`platform` | `desktop` | `byo`),
  `client_id` Text, `client_secret_enc` Text.
- `gmail_accounts` +: `oauth_client_id` FK nulo, `refresh_token_enc` Text nulo,
  `scopes` Text nulo (separados por espaço), `last_sync_at` timestamptz nulo,
  `last_error` Text nulo. `status` ∈ `connected` | `disconnected` | `revoked` | `template`.

Código:
- `src/platform/secrets.py`: `seal(plain: str) -> str`, `unseal(token: str) -> str`.
  `MultiFernet` com as chaves de `APP_SECRET` (vírgula separa chaves; a 1ª cifra).
  `unseal` de token inválido → `ValueError`.
- `src/gmail/clients.py`:
  - `class GmailRevoked(RuntimeError)`.
  - `class GmailClients`: `__init__(self, sf: sessionmaker[Session])`;
    `service_for(self, account_id: int) -> Resource`;
    `current(self) -> Resource` (= `service_for(current_account())`);
    `has_scope(self, scope: str) -> bool` (conta corrente);
    `getter(self) -> Callable[[], Resource]` (= `self.current`, para os consumidores
    que já recebem função sem argumento).
  - Refresh renovado volta cifrado ao banco. `invalid_grant` → conta `revoked`,
    `last_error`, levanta `GmailRevoked`.
- `src/gmail/oauth.py`:
  - `OAuthPurpose = Literal["connect", "login", "signup"]`.
  - `class OAuthState(BaseModel)`: `purpose`, `user_id: int | None`, `verifier: str`,
    `invite_token: str | None`, `exp: int`. Serializado e cifrado com `seal`; vida 10 min.
  - `class GoogleIdentity(BaseModel)`: `email: str` (minúsculo), `email_verified: bool`,
    `sub: str`, `refresh_token: str | None`, `scopes: list[str]`.
  - `GMAIL_SCOPES` (openid email profile gmail.modify gmail.settings.basic) e
    `LOGIN_SCOPES` (openid email).
  - `authorization_url(purpose: OAuthPurpose, *, user_id: int | None, invite_token: str | None) -> str`
    (PKCE S256; `connect`/`signup` pedem `GMAIL_SCOPES`, `access_type=offline`,
    `prompt=consent`; `login` pede `LOGIN_SCOPES`, `prompt=select_account`).
  - `exchange(code: str, state: str) -> tuple[OAuthState, GoogleIdentity]`.
  - Callback registry: `CallbackHandler = Callable[[OAuthState, GoogleIdentity, ApiDeps], Response]`;
    `register_callback(purpose: OAuthPurpose, handler: CallbackHandler) -> None`.
- Rotas (`src/api/routes/oauth.py`): `POST /api/oauth/google/connect` (logado) →
  `{"url": ...}`; `GET /api/oauth/google/callback` despacha por `state.purpose`.
  Fase 2 registra só `connect` (reconectar: e-mail do Google tem de ser o da conta).
- `src/gmail/accounts_store.py` (system scope, só ids/metadados):
  `save_connection(sf, account_id: int, identity: GoogleIdentity, oauth_client_id: int) -> None`,
  `account_id_by_email(sf, email: str) -> int | None`,
  `mark_status(sf, account_id: int, status: str, error: str | None) -> None`,
  `accounts_needing_renewal(sf, now_ms: int, within_ms: int) -> list[int]`.
- `src/gmail/lifecycle.py`: `stop_watch(clients: GmailClients, account_id: int) -> None`,
  `revoke_token(sf, account_id: int) -> None` (POST oauth2.googleapis.com/revoke).
  Fase 6 usa os dois.
- Webhook roteado por `emailAddress` → `SyncCoordinators.trigger(account_id)`
  (`src/services/sync.py`: `class SyncCoordinators` com um `SyncCoordinator` por conta).
  Desconhecido/não conectado → 204 + log.
- Laço de renovação percorre contas `connected` e renova as que vencem em < 2 dias;
  falha numa conta não afeta outras.
- `GET /api/gmail/status` → `{"email_address", "status", "last_sync_at", "last_error"}`.
- CLI ponte: `python -m src.cli.gmail_import_token --account <email>` importa
  `secrets/token.json` + `credentials.json` (linha `oauth_clients.kind='desktop'`)
  para a conta, para não haver janela sem Gmail entre deploy e reconexão web.
  Linha `platform` de `oauth_clients` vem de env `GOOGLE_OAUTH_CLIENT_ID` /
  `GOOGLE_OAUTH_CLIENT_SECRET` (CLI `python -m src.cli.oauth_client sync`).
- Painel: cartão "Gmail" em Segurança com status e botão "Reconectar Gmail".

## Fase 3 — Login e entrada (produz)

Migração 0023: `users` + `google_sub` (único, nulo), `onboarding_step` String(16) nulo,
`password_hash` e `totp_secret_enc` nulos; `invites` (`id`, `email` único minúsculo,
`token_hash`, `created_by`, `created_at`, `accepted_at`, `revoked_at`);
bloqueio global vira por usuário-alvo.
- `src/auth/invites.py`: `create_invite(sf, email: str, created_by: int) -> str` (link),
  `invite_for_token(sf, raw: str) -> InviteRow | None`, `accept(sf, invite_id: int) -> None`.
- Handlers `signup` e `login` registrados no registry da fase 2.
- Onboarding passos 1–4 (`onboarding_step`: `gmail` → `totp` → `password_optional` → `done_basic`).
  Passos 5 e 6 pertencem às fases 4 e 5; a fase 3 deixa `done_basic` como fim e
  expõe `ONBOARDING_STEPS: list[str]` em `src/auth/onboarding.py` para elas estenderem.
- CLI `python -m src.cli.invite <email>`; `create_user` sai.

## Fase 4 — Chaves e fila (produz)

Migração 0024: `user_llm_keys` (`user_id` PK, `app_key_enc`, `backfill_key_enc` nulo,
`validated_at`, `last_error`).
- `src/llm/keys.py`: `keys_for_account(sf, account_id: int) -> AccountKeys | None`,
  `validate_key(key: str) -> str | None` (None = ok; texto = erro para leigo).
- Gateways por chave com cache (expiração livre); onboarding passo 5 (`llm_key`).
- Fila justa: claim em rodízio por conta; espera de cota sem prender a faixa.
- CLI `python -m src.cli.import_env_keys --account <email>`.

## Fase 5 — Painel (produz)

Configurações, avisos (Gmail revogado, chave inválida), admin (convites, saúde
operacional **sem conteúdo**, reset de 2FA), passo 6 do onboarding, `/api/security/*`
e `/api/auth/sessions` filtrados por usuário (admin vê só o operacional).

## Fase 6 — Saída (produz)

- `src/accounts/exit.py`: `disconnect(sf, clients, account_id: int) -> None`
  (stop_watch + revoke_token + status `disconnected`);
  `delete_account(sf, clients, user_id: int) -> None` (para watch, revoga, apaga numa
  transação todas as linhas da conta e do usuário; convite fica).
- Tela de exclusão mostra a retenção de backups (levantar em `deploy/deploy.sh`: 7 dumps).
- Teste obrigatório: depois de excluir A, nenhuma tabela tem linha com a conta de A.
