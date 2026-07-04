#!/usr/bin/env bash
# ============================================================================
# Tokio — Aplica migrations Supabase (Postgres) de forma idempotente.
#
# - Lê DATABASE_URL do .env (em /home/tokio/Tokio/.env)
# - Cria tabela de controle schema_migrations_supabase se não existir
# - Aplica em ordem alfabética todo db/migrations/supabase/*.sql ainda não
#   registrado na tabela de controle
# - Falha de UM arquivo: loga no stderr, NÃO derruba o deploy (exit 0)
# ============================================================================

set -uo pipefail

APP_DIR="/home/tokio/Tokio"
ENV_FILE="$APP_DIR/.env"
MIGRATIONS_DIR="$APP_DIR/db/migrations/supabase"

# Carrega DATABASE_URL do .env sem imprimir secrets
if [ ! -f "$ENV_FILE" ]; then
  echo "[supabase-migrations] .env não encontrado em $ENV_FILE" >&2
  exit 0
fi

DATABASE_URL="$(set -a && . "$ENV_FILE" && echo "$DATABASE_URL")"
if [ -z "${DATABASE_URL:-}" ]; then
  echo "[supabase-migrations] DATABASE_URL não definido no .env" >&2
  exit 0
fi

# Garante psql disponível
if ! command -v psql >/dev/null 2>&1; then
  echo "[supabase-migrations] psql não encontrado — pulando migrations Supabase" >&2
  exit 0
fi

# Tabela de controle
echo "[supabase-migrations] criando tabela de controle schema_migrations_supabase..."
psql "$DATABASE_URL" -q -c "
CREATE TABLE IF NOT EXISTS schema_migrations_supabase (
  filename    TEXT PRIMARY KEY,
  applied_at  TIMESTAMPTZ DEFAULT now()
);
" >/dev/null 2>&1 || {
  echo "[supabase-migrations] ERRO ao criar tabela de controle — continuando" >&2
  exit 0
}

if [ ! -d "$MIGRATIONS_DIR" ]; then
  echo "[supabase-migrations] diretório $MIGRATIONS_DIR não existe — nada a aplicar"
  exit 0
fi

# Lista migrations em ordem alfabética
mapfile -t FILES < <(find "$MIGRATIONS_DIR" -maxdepth 1 -type f -name '*.sql' | sort)

if [ "${#FILES[@]}" -eq 0 ]; then
  echo "[supabase-migrations] nenhuma migration .sql encontrada em $MIGRATIONS_DIR"
  exit 0
fi

APPLIED=0
SKIPPED=0
FAILED=0

for f in "${FILES[@]}"; do
  fname="$(basename "$f")"

  # Verifica se já está registrado
  already="$(psql "$DATABASE_URL" -t -A -q -c \
    "SELECT 1 FROM schema_migrations_supabase WHERE filename = '$fname';" 2>/dev/null)"

  if [ "$already" = "1" ]; then
    SKIPPED=$((SKIPPED + 1))
    continue
  fi

  echo "[supabase-migrations] aplicando $fname..."
  if psql "$DATABASE_URL" -q -v ON_ERROR_STOP=1 -f "$f" >/dev/null 2>&1; then
    psql "$DATABASE_URL" -q -c \
      "INSERT INTO schema_migrations_supabase (filename) VALUES ('$fname') ON CONFLICT DO NOTHING;" \
      >/dev/null 2>&1 || {
        echo "[supabase-migrations] AVISO: $fname aplicada mas não registrada no tracking" >&2
    }
    APPLIED=$((APPLIED + 1))
  else
    echo "[supabase-migrations] ERRO ao aplicar $fname (pulando)" >&2
    FAILED=$((FAILED + 1))
  fi
done

echo "[supabase-migrations] concluído — applied=$APPLIED skipped=$SKIPPED failed=$FAILED"

# Falha de migrations NÃO derruba o deploy
exit 0
