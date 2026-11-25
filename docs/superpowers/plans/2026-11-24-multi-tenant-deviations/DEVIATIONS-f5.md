# Fase 5 (painel) — desvios, stubs e to-dos do integrador

Branch `feat/mt-f5`, criado a partir de `feat/multi-tenant-rest` (211f827, que tem o contrato).

## Desvios do contrato

1. **Migração**: `0025_panel_admin.py` tem `down_revision = "0024"` (não `0021`). Neste branch a
   `0024` é um **stub** (`0024_stub_f2_f3_f4_deps.py`, `down_revision = "0021"`) que cria o que as
   fases 2-4 entregam e o painel consome. No merge: **apagar o stub inteiro**; a 0025 já aponta
   para a revisão certa. Motivo: manter a cadeia do contrato (0022-0024 reservadas) sem
   duplicar colunas na 0025.
2. `users.onboarding_step` etc.: ver stubs abaixo. O fim do onboarding é `onboarding_step = NULL`
   (suposição; ajustar se a fase 3 usar outro marcador).
3. Reset de 2FA grava `totp_secret_enc = ""` (a coluna ainda é NOT NULL aqui). Com a 0023 a
   coluna aceita nulo: trocar por `None` em `AuthService.reset_totp`. Até a fase 3 existir,
   um usuário resetado não consegue refazer o TOTP (não há tela de cadastro neste branch).
4. `GET /api/auth/me` ganhou `role`, `email`, `onboarding_step` (teste existente atualizado).
5. `/api/security/blocks` devolve vazio para não-admin (200, não 403), para não quebrar a
   página; o painel de eventos da plataforma do admin é `GET /api/admin/security/events`.
6. Aviso "backfill não iniciado" (spec) **não** foi implementado: o pedido da fase listava só
   Gmail revogado e chave inválida.
7. `ops._start` virou `start_backfill` (público) para o passo 6 reutilizar.
8. Tela `/onboarding` (`pages/Onboarding/Conectar.tsx`) é mínima: só existe para o redirect de
   409 `no_account` não entrar em loop. A fase 3 a substitui pelo fluxo real.
9. Troca de TOTP: `POST /api/auth/totp` exige senha (se houver) + código atual e troca na hora
   (sem confirmar o novo código), devolvendo segredo e 10 recovery codes. QR não é desenhado
   (sem lib de QR no web): mostra a chave e um link `otpauth://`.

## Stubs (todos marcados `STUB(fase N): substituído no merge`)

| Arquivo | Fase | Observação |
|---|---|---|
| `alembic/versions/0024_stub_f2_f3_f4_deps.py` | 2-4 | apagar no merge |
| `src/db/models.py`: `GmailAccount.last_sync_at/last_error`, `User.onboarding_step`, `Invite`, `UserLlmKey` | 2, 3, 4 | pegar a versão das fases |
| `src/platform/secrets.py` (`seal`/`unseal`) | 2 | |
| `src/auth/onboarding.py` (`ONBOARDING_STEPS`) | 3 | o passo `finish` é anexado por `src/accounts/finish.py` (idempotente), não pelo arquivo da fase 3 |
| `src/auth/invites.py` | 3 | `create_invite` devolve link relativo `/entrar?convite=...`; o web prefixa a origem |
| `src/api/routes/admin_invites.py` (POST/GET `/api/admin/invites`) | 3 | apagar se a fase 3 já tiver as rotas; o GET devolve `{id,email,state,created_at}` (`state`: pending/accepted/revoked), `web/src/api/schemas.ts#AdminInvite` espera isso |
| `src/llm/keys.py` (`keys_for_account`, `validate_key`) | 4 | |

Web: `GET /api/gmail/status` e `POST /api/oauth/google/connect` (fase 2) são só mockados nos testes.

## To-dos do integrador

- Apagar a 0024 stub; conferir que 0025 aplica sobre a 0024 real (`alembic upgrade head`, `downgrade base`, `upgrade head`).
- Resolver conflitos em `src/db/models.py` pegando as definições das fases 2-4 (só `SecurityEvent.user_id` é desta fase).
- Rota duplicada de convites: manter uma só (`DELETE /api/admin/invites/{id}` é desta fase em `admin.py`).
- `src/platform/secrets.py` real usa `APP_SECRET` multi-chave; `src/accounts/llm_keys.py` só usa `seal`.
- Se a fase 4 expuser rota própria para salvar chave no onboarding, unificar com `PUT /api/settings/llm-keys`.
- Fase 6 reaproveita o cartão "Gmail" de Configurações para "Desconectar" / "Excluir conta".
- Fase 3: a rota web `/onboarding` e o avanço `done_basic -> finish` (quem leva o usuário a `/onboarding/pronto`).
