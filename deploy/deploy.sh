#!/usr/bin/env bash
# Deploy one image tag to this VPS, with DB backup, smoke test and rollback.
#
# Runs as the forced command of the CI deploy key (authorized_keys
# `command="…/deploy/deploy.sh"`): the only accepted input is
# `deploy <40-hex-sha>`, read from SSH_ORIGINAL_COMMAND (or argv locally).
#
# Exit codes: 0 deployed; 1 failed (rolled back when possible); 2 bad input.
set -euo pipefail

# --- input (validated before touching anything) ------------------------------
if [[ -n "${SSH_ORIGINAL_COMMAND:-}" ]]; then
    read -r -a words <<<"$SSH_ORIGINAL_COMMAND"
    if [[ ${#words[@]} -ne 2 || "${words[0]}" != "deploy" ]]; then
        echo "comando não permitido" >&2
        exit 2
    fi
    sha="${words[1]}"
else
    sha="${1:-}"
fi
if [[ ! "$sha" =~ ^[0-9a-f]{40}$ ]]; then
    echo "SHA inválido: esperado 40 caracteres hexadecimais minúsculos" >&2
    exit 2
fi

# --- setup ---------------------------------------------------------------------
APP_DIR="$(cd "${BASH_SOURCE[0]%/*}/.." && pwd)"
IMAGE="ghcr.io/jsouza181gh/gmail-integration"
PROJECT="gmail-integration"
STATE_DIR="$APP_DIR/.deploy"
BACKUP_DIR="$APP_DIR/backups"
BACKUPS_KEPT=7
SMOKE_TIMEOUT_S="${SMOKE_TIMEOUT_S:-120}"
SMOKE_INTERVAL_S="${SMOKE_INTERVAL_S:-5}"

mkdir -p "$STATE_DIR" "$BACKUP_DIR"
prev="$(cat "$STATE_DIR/current" 2>/dev/null || true)"

log() { echo "[deploy $(date +%H:%M:%S)] $*"; }

# The compose file travels inside the image, so every tag runs with the
# compose file it was built with (also on rollback).
compose_file() {
    local tag="$1" file="$STATE_DIR/compose-$1.yml"
    if [[ ! -s "$file" ]]; then
        docker run --rm --entrypoint cat "$IMAGE:$tag" /app/docker-compose.yml >"$file.tmp"
        mv "$file.tmp" "$file"
    fi
    echo "$file"
}

compose() {
    local tag="$1"
    shift
    APP_TAG="$tag" docker compose -p "$PROJECT" --project-directory "$APP_DIR" \
        -f "$(compose_file "$tag")" "$@"
}

health() {
    local tag="$1" service="$2" cid
    cid="$(compose "$tag" ps -q "$service" || true)"
    docker inspect --format '{{.State.Health.Status}}' "$cid" 2>/dev/null || true
}

# Healthy api (+ /healthz) and, when the tag's compose defines it, a healthy
# worker. Older tags have no worker, so rolling back to them still works.
smoke() {
    local tag="$1" deadline=$((SECONDS + SMOKE_TIMEOUT_S)) want_worker=0
    if compose "$tag" config --services 2>/dev/null | grep -qx worker; then
        want_worker=1
    fi
    while ((SECONDS < deadline)); do
        if [[ "$(health "$tag" api)" == "healthy" ]] \
            && curl -fsS -o /dev/null http://127.0.0.1:8000/healthz \
            && { ((want_worker == 0)) || [[ "$(health "$tag" worker)" == "healthy" ]]; }; then
            return 0
        fi
        sleep "$SMOKE_INTERVAL_S"
    done
    return 1
}

backup() {
    if ! docker ps --format '{{.Names}}' | grep -q "^${PROJECT}-db-"; then
        log "banco ainda não está rodando; sem backup"
        return 0
    fi
    local file="$BACKUP_DIR/gmail-$(date +%Y%m%d-%H%M%S)-pre-${sha:0:7}.sql.gz"
    docker exec "$(docker ps -q --filter "name=^${PROJECT}-db-")" \
        pg_dump -U gmail -d gmail | gzip >"$file"
    log "backup: $(basename "$file")"
    ls -1t "$BACKUP_DIR"/gmail-*.sql.gz | tail -n +$((BACKUPS_KEPT + 1)) | xargs -r rm --
}

rollback() {
    if [[ -z "$prev" ]]; then
        log "FALHOU e não há versão anterior para rollback"
        exit 1
    fi
    log "FALHOU; rollback para ${prev:0:7}"
    compose "$prev" up -d --remove-orphans
    if smoke "$prev"; then
        log "rollback para ${prev:0:7} concluído; produção na versão anterior"
    else
        log "ROLLBACK TAMBÉM FALHOU; intervenção manual necessária"
    fi
    exit 1
}

# --- deploy --------------------------------------------------------------------
log "deploy ${sha:0:7} (atual: ${prev:0:7})"
docker image inspect "$IMAGE:$sha" >/dev/null 2>&1 || docker pull -q "$IMAGE:$sha" >/dev/null
backup
# `up` runs migrate first (api depends on it finishing successfully).
compose "$sha" up -d --remove-orphans || rollback
smoke "$sha" || rollback

echo "$sha" >"$STATE_DIR/current"
ls -1t "$STATE_DIR"/compose-*.yml | tail -n +6 | xargs -r rm --
log "deploy ${sha:0:7} ok"
