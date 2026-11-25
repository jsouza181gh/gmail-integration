# Fase 3 — desvios do contrato e pontos para o integrador

Branch `feat/mt-f3`. Tudo abaixo é o menor desvio possível; nada contradiz o contrato
nos nomes que as outras fases consomem.

## Desvios

1. **Diretório de migrações**: o contrato diz `migrations/versions/`; no repo é
   `alembic/versions/`. A migração é `alembic/versions/0023_login_invites.py`.
2. **`down_revision = "0021"`** (a 0022 da fase 2 não existe neste branch). O integrador
   troca para `"0022"` (uma linha) e a docstring.
3. **Stubs da fase 2** (marcados `# STUB(fase 2): substituído no merge`):
   `src/platform/__init__.py`, `src/platform/secrets.py`, `src/gmail/oauth.py`,
   `src/gmail/accounts_store.py`. Ao integrar, a versão da fase 2 vence em todos
   (aceitar "theirs"); conferir só que `register_callback(purpose, handler)` e
   `GoogleIdentity`/`OAuthState` têm os campos do contrato. O stub de `save_connection`
   só marca a conta como `connected`.
4. **`platform_oauth_client_id(sf)`** (`src/api/google_entry.py`): `save_connection` pede
   `oauth_client_id` e o contrato não diz como achá-lo. Uso SQL cru
   (`SELECT id FROM oauth_clients WHERE kind='platform'`) porque o modelo `OAuthClient`
   é da fase 2. Quando o modelo existir, trocar por um `select(OAuthClient.id)`.
   Os testes substituem a função.
5. **Cópia da configuração genérica (decisão 7 da spec)** não estava em nenhuma fase do
   contrato; sem ela a conta nova nasce sem taxonomia nem agente. Implementei em
   `src/db/seed_account.py` (`seed_generic_config`), chamada pelo `signup`: copia só a
   taxonomia ativa, o `agent_config` ativo e as `tool_policies` da conta `template`
   (instalação nova) ou, na produção, da conta do primeiro admin. Regras, KB, labels e
   histórico não são copiados.
6. **Passo `gmail`**: o consentimento do Google já conecta o Gmail, então o `signup` cria
   o usuário direto em `onboarding_step = "totp"`. O passo `gmail` só aparece para quem
   ficar sem Gmail (a página manda reabrir o convite). Os códigos de recuperação são
   exibidos dentro do passo `totp` (confirmar código → mostrar códigos → "guardei" →
   `password_optional`), para não inventar um passo fora da lista do contrato.
7. **`onboarding_step = NULL` significa "concluído"** (usuários anteriores aos convites,
   inclusive o João). O João continua entrando exatamente como antes.
8. **Sessão durante o onboarding**: novo cookie `onb` (JWT `typ=onb`, 1 h, path `/api`)
   emitido após a prova do Google, que só abre `/api/onboarding/*`. A sessão completa
   (access/refresh) só nasce quando o TOTP é cadastrado e os códigos são reconhecidos.
   O `onb` vem com seu próprio cookie CSRF.
9. **Bloqueio por usuário**: colunas `users.locked_at` / `users.lock_cleared_at` e
   `login_failures.user_id` (a migração também as cria). 3+ IPs distintos falhando
   contra o mesmo usuário em 24 h fecham **aquele** usuário; bloqueio por IP segue
   global. Consequências:
   - `AuthService.unlock_global()` e o estado `"global_lock"` de `check_open` saíram;
     entram `unlock_user`, `user_locked`, `locked_users`, `find_user`, `google_step`,
     `start_session`. CLI: `python -m src.cli.unlock --user <email>` no lugar de `--global`.
   - `GET /api/security/blocks` mantém o campo `global_lock` (o painel atual o usa) com
     o significado "algum usuário está fechado". A fase 5 refaz isso.
   - Novo `SecurityKind` `user_lock` (alerta reaproveita o tipo `global_lock`), mais
     `invite_created`, `signup`, `onboarding_done`. A linha `global_lock` de
     `app_settings` fica no banco, sem uso.
   - O callback do Google não recebe o IP (assinatura do contrato), então o bloqueio de IP
     é verificado na etapa do código (`/api/auth/2fa`), que vem logo depois.
10. **Login por senha** agora exige senha **e** TOTP cadastrados; também aceita e-mail no
    campo de usuário. Convidados usam o e-mail como `username`.
11. **`/api/auth/me`** ganhou `role`, `onboarding_step`, `onboarding_done`.
12. **Convites**: `create_invite(sf, email, created_by, *, now=None, origin=None)` (kwargs
    extras opcionais; `origin` cai em `APP_ORIGIN`). Extras além do contrato:
    `revoke`, `list_invites`, `open_invite_for` (a checagem única de entrada: para abrir o
    cadastro, é só ela que muda), `InviteRow.status`.
13. **API admin** em `src/api/routes/admin_invites.py` (não em `admin.py`, para não colidir
    com a fase 5): `POST/GET /api/admin/invites` e `DELETE /api/admin/invites/{id}`
    (revogar; a UI da fase 5 precisa).
14. **`create_user` removido**: a lógica de TOTP/códigos foi para `src/auth/enroll.py`;
    os testes usam `tests/auth_fakes.make_user`. README atualizado para `src.cli.invite`.
15. **Imagem do aviso do Google**: a página do convite desenha as duas telas ("O Google
    não verificou este app" → "Avançado"; "Acessar Inbox Agent (não seguro)") em HTML/CSS
    em vez de screenshot (não consigo capturar as telas reais daqui). Trocar por prints
    reais é só substituir `web/src/pages/Entrada/GoogleWarning.tsx`.

## Para o integrador

- Rotas novas: `entry` (`/api/entry/*`), `onboarding`, `admin_invites`, registradas em
  `src/api/app.py` (`create_app`), junto com `register_entry_callbacks()` — que usa o
  registry da fase 2. A rota de callback `GET /api/oauth/google/callback` é da fase 2.
- `NO_SESSION_WRITES` (src/api/security.py) ganhou `/api/entry/{invite,signup,login}`.
- `mypy.ini` lista arquivos explicitamente: conferir se as listas das fases se juntam.
- Não testei em Postgres (CI roda `tests/test_multitenant_pg.py`); `seed_generic_config` e
  o signup usam `system_scope()` e só tocam `gmail_accounts`, `users`, `invites` e as
  tabelas de configuração da conta nova.
