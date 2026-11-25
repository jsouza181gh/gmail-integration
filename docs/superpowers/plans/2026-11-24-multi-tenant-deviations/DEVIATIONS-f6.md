# Phase 6 deviations

1. **Backup retention is stated by count, not days.** `deploy/deploy.sh` keeps the last
   `BACKUPS_KEPT=7` dumps, and one dump is made per deploy, so a deleted account leaves the
   backups only after 7 more deploys; there is no fixed number of days. The screen says
   "7 cópias mais recentes ... depois de 7 atualizações do sistema" instead of "em até N dias".
   `BACKUP_COPIES_KEPT` in `src/accounts/exit.py` mirrors the script; a test fails if they diverge.
2. **No migration `0026`.** Deletion issues explicit DELETEs in FK order; nothing needed ON DELETE changes.
3. **Wrong re-confirmation answers 403, not 401.** The panel client refreshes and retries on 401,
   which would count each wrong password twice toward the IP lockout.
4. **Page `/conta` (new), not a Segurança card**, to avoid conflicts with phase 5's Segurança rework.
   Added a "Conta" entry to `SideNav`, `MoreSheet` and `BottomNav`'s system paths.
5. **`SecurityKind` gained `account_deleted` and `account_disconnected`** (and web labels).
6. **Invites** do not exist on this branch (phase 3). Deletion never touches an invites table, so
   they are kept by construction; the integrator should add an assertion once `invites` exists
   (see to-dos in the final report).
7. **User-owned platform rows are derived** (every table with a FK to `users.id`, besides `users`):
   today `auth_sessions` and `recovery_codes`; phase 4's `user_llm_keys` is covered if it has that FK.
   `login_failures` / `security_events` / `ip_blocks` are keyed by IP, not by user, and stay.

## Stubs (replaced at merge with phase 2)

`src/gmail/clients.py`, `src/gmail/lifecycle.py`, `src/gmail/accounts_store.py` (only `mark_status`,
which does not yet persist `error` because `last_error` arrives with migration 0022).
