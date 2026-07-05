#!/usr/bin/env bash
# SQLite backup versionado do Tokio.
#
# Fonte de verdade: SQLite local. Este script cria snapshot consistente via
# sqlite3 .backup, compacta, valida sob demanda e opcionalmente envia offsite.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
ENV_FILE="${ENV_FILE:-$APP_DIR/.env}"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

DB_PATH="${DB_PATH:-$APP_DIR/data/tokio.db}"
BACKUP_DIR="${BACKUP_DIR:-$APP_DIR/data/backups}"
LOCAL_RETENTION_DAYS="${LOCAL_RETENTION_DAYS:-7}"
REMOTE_RETENTION_DAYS="${REMOTE_RETENTION_DAYS:-30}"

log() { echo "[tokio-backup] $*"; }
warn() { echo "[tokio-backup] WARN: $*" >&2; }

latest_backup() {
  find "$BACKUP_DIR" -maxdepth 1 -type f -name 'tokio-*.db.gz' -print 2>/dev/null \
    | sort | tail -n 1
}

verify_backup() {
  local src="${1:-$(latest_backup)}"
  if [ -z "${src:-}" ] || [ ! -f "$src" ]; then
    echo "backup não encontrado para verificação" >&2
    return 1
  fi
  local tmp
  tmp="$(mktemp -t tokio-restore.XXXXXX.db)"
  trap 'rm -f "$tmp"' RETURN
  gzip -dc "$src" > "$tmp"
  local integrity
  integrity="$(sqlite3 "$tmp" 'PRAGMA integrity_check;')"
  if [ "$integrity" != "ok" ]; then
    echo "integrity_check falhou: $integrity" >&2
    return 1
  fi
  local has_strategies
  has_strategies="$(sqlite3 "$tmp" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='strategies';")"
  if [ "$has_strategies" != "1" ]; then
    echo "tabela strategies ausente no restore" >&2
    return 1
  fi
  sqlite3 "$tmp" 'SELECT COUNT(*) FROM strategies;' >/dev/null
  log "verify ok: $(basename "$src")"
}

copy_offsite() {
  local artifact="$1"
  local remote="${BACKUP_REMOTE:-}"
  if [ -z "$remote" ]; then
    warn "BACKUP_REMOTE ausente — backup offsite pulado"
    return 0
  fi

  if [[ "$remote" == file://* ]]; then
    local dest="${remote#file://}"
    mkdir -p "$dest"
    cp "$artifact" "$dest/"
    find "$dest" -maxdepth 1 -type f -name 'tokio-*.db.gz' \
      -mtime +"$REMOTE_RETENTION_DAYS" -delete
    log "offsite file:// ok: $dest/$(basename "$artifact")"
    return 0
  fi

  if [[ "$remote" == scp://* ]]; then
    local spec="${remote#scp://}"
    local host_path="${spec#*/}"
    local host="${spec%%/*}"
    if [ "$host" = "$spec" ] || [ -z "$host_path" ]; then
      echo "BACKUP_REMOTE scp inválido: $remote" >&2
      return 1
    fi
    scp "$artifact" "$host:$host_path/"
    ssh "$host" "find '$host_path' -maxdepth 1 -type f -name 'tokio-*.db.gz' -mtime +$REMOTE_RETENTION_DAYS -delete" || true
    log "offsite scp ok: $host:$host_path/$(basename "$artifact")"
    return 0
  fi

  if command -v rclone >/dev/null 2>&1; then
    rclone copy "$artifact" "$remote"
    rclone delete --min-age "${REMOTE_RETENTION_DAYS}d" "$remote" --include 'tokio-*.db.gz'
    log "offsite rclone ok: $remote/$(basename "$artifact")"
    return 0
  fi

  echo "BACKUP_REMOTE definido, mas rclone não está instalado: $remote" >&2
  return 1
}

if [ "${1:-}" = "--verify" ]; then
  verify_backup "${2:-}"
  exit 0
fi

command -v sqlite3 >/dev/null 2>&1 || { echo "sqlite3 não encontrado" >&2; exit 1; }
command -v gzip >/dev/null 2>&1 || { echo "gzip não encontrado" >&2; exit 1; }

if [ ! -f "$DB_PATH" ]; then
  echo "SQLite não encontrado: $DB_PATH" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
stamp="$(date -u +%Y%m%d-%H%M%S)"
raw="$BACKUP_DIR/tokio-$stamp.db"
artifact="$raw.gz"

sqlite3 "$DB_PATH" ".backup $raw"
gzip -f "$raw"
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'tokio-*.db.gz' \
  -mtime +"$LOCAL_RETENTION_DAYS" -delete

verify_backup "$artifact"
copy_offsite "$artifact"
log "backup ok: $artifact"
