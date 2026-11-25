# Desvios do contrato (fase 2)

- Diretório de migrações: o repo usa `alembic/versions/`, não `migrations/versions/`. Arquivo: `alembic/versions/0022_gmail_per_account.py` (down_revision `0021`).
- `create_app(trigger=...)`: `trigger` ficou opcional e entraram `syncs: SyncCoordinators | None` e `resolve_account: Callable[[str], int | None] | None`. Com `syncs` o push vai só para a conta do `emailAddress`; sem ele (testes antigos, instância mínima) `trigger` roda a cada push.
- `account_id_by_email` (accounts_store) devolve só conta `connected`; conta desconhecida ou não conectada dá `None` (webhook: 204 + log).
- `GmailClients.__init__` aceita também (só por nome) `refresh`, `build_service` e `on_revoked`; `on_revoked` é atributo público ligado ao alerta ao admin (`gmail_revoked_hook`, novo `AlertKind` `gmail_revoked`). Além de `GmailRevoked` existe `GmailNotConnected` (conta sem token/escopo).
- `src/gmail/oauth.py`: `authorization_url`/`exchange` não recebem `sf`; leem uma configuração de módulo definida por `oauth.configure(sf, origin, http=None)` (chamada por `create_app` quando há `api`). `OAuthPurpose` é o `Literal` do contrato; `HttpForm` e `read_state` são extras. Callback: se o state é válido mas o purpose não tem handler, redireciona com erro.
- Extras em `accounts_store`: `save_refresh_token`, `account_email`, `mark_synced`, `syncable_account_ids`. Novo `src/gmail/oauth_clients.py` (`upsert_client`, `platform_client`, `client_by_id`).
- `ApiDeps` ganhou `on_connected: Callable[[int], None]` (reconexão pelo painel inicia o watch e dispara o sync daquela conta).
- `GET /api/gmail/status`: conta `connected` sem token (a do João antes da ponte) é reportada como `disconnected` com a mensagem "clique em Reconectar Gmail".
- Início da API e laço diário só renovam contas com watch vencendo em < 2 dias ou nunca iniciado (antes o início sempre reiniciava o watch).
- `src/gmail/client.py` ficou só com as constantes de escopo; `get_gmail_service`/`has_scope`/`save_credentials` saíram. `src/gmail/authorize.py` (fluxo Desktop) segue existindo para gerar o token que a ponte importa.
- `web/test/setup.ts`: `asyncUtilTimeout` 5 s (primeira renderização de cada arquivo estoura 1 s em máquina lenta).
- `mypy.ini` lista arquivos explicitamente: os novos foram adicionados.
