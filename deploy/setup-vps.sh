#!/usr/bin/env bash
# One-time VPS setup for the CI deploy (idempotent). Run with sudo:
#   sudo bash setup-vps.sh <ci-public-key-file> <deploy.sh> [source-app-dir]
# Creates the `deploy` user, the app dir, copies .env and the Gmail token from
# the existing install and authorizes the CI key restricted to deploy.sh.
set -euo pipefail

PUB="${1:?chave pública do CI}"
SCRIPT="${2:?caminho do deploy.sh}"
SRC="${3:-/home/ubuntu/apps/gmail-integration}"
USER_NAME=deploy
HOME_DIR="/home/$USER_NAME"
APP="$HOME_DIR/gmail-integration"

[[ $EUID -eq 0 ]] || { echo "rode com sudo" >&2; exit 1; }
[[ -f "$SRC/.env" && -f "$SRC/secrets/token.json" ]] || { echo "faltam .env/token.json em $SRC" >&2; exit 1; }

id "$USER_NAME" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "$USER_NAME"
usermod -aG docker "$USER_NAME"
uid="$(id -u "$USER_NAME")"
gid="$(id -g "$USER_NAME")"

install -d -o "$USER_NAME" -g "$USER_NAME" -m 750 "$APP" "$APP/deploy" "$APP/secrets" "$APP/backups"
install -o "$USER_NAME" -g "$USER_NAME" -m 755 "$SCRIPT" "$APP/deploy/deploy.sh"

if [[ ! -f "$APP/.env" ]]; then
    install -o "$USER_NAME" -g "$USER_NAME" -m 600 "$SRC/.env" "$APP/.env"
fi
sed -i "s/^APP_UID=.*/APP_UID=$uid/; s/^APP_GID=.*/APP_GID=$gid/" "$APP/.env"
grep -q '^APP_UID=' "$APP/.env" || printf 'APP_UID=%s\nAPP_GID=%s\n' "$uid" "$gid" >>"$APP/.env"

if [[ ! -f "$APP/secrets/token.json" ]]; then
    install -o "$USER_NAME" -g "$USER_NAME" -m 600 "$SRC/secrets/token.json" "$APP/secrets/token.json"
fi

install -d -o "$USER_NAME" -g "$USER_NAME" -m 700 "$HOME_DIR/.ssh"
key="$(tr -d '\r\n' <"$PUB")"
[[ "$key" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+(\ .*)?$ ]] || { echo "chave pública inválida" >&2; exit 1; }
printf 'command="%s",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding %s\n' \
    "$APP/deploy/deploy.sh" "$key" >"$HOME_DIR/.ssh/authorized_keys"
chown "$USER_NAME:$USER_NAME" "$HOME_DIR/.ssh/authorized_keys"
chmod 600 "$HOME_DIR/.ssh/authorized_keys"

echo "ok: $USER_NAME (uid $uid) em $APP"
