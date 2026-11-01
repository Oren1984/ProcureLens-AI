#!/usr/bin/env bash
# Restore a backup into an ISOLATED database (default: procurelens_restore_check) and print
# row counts for verification. Restoring over the live database requires --into-main and a
# stopped app, and is a deliberate manual decision.
#   ./scripts/restore.sh backups/<file>.dump [--target NAME] [--into-main]
set -euo pipefail
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1
DUMP="${1:?usage: restore.sh <dump> [--target NAME] [--into-main]}"; shift
TARGET="procurelens_restore_check"; INTO_MAIN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="$2"; shift 2;;
    --into-main) INTO_MAIN=1; shift;;
    *) echo "unknown option $1" >&2; exit 2;;
  esac
done
[[ "$TARGET" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || { echo "Invalid target name." >&2; exit 2; }
MAIN=$(docker compose exec -T db sh -c 'echo -n "$POSTGRES_DB"')
if [[ "$TARGET" == "$MAIN" || $INTO_MAIN -eq 1 ]]; then
  if [[ $INTO_MAIN -ne 1 ]]; then echo "Refusing to restore over '$MAIN' without --into-main." >&2; exit 1; fi
  if docker compose ps --status running --services | grep -qx app; then
    echo "Stop the app first: docker compose stop app" >&2; exit 1
  fi
  TARGET="$MAIN"
  echo "Restoring OVER the main database '$MAIN' (explicitly requested)."
  docker compose exec -T db sh -c "pg_restore -U \"\$POSTGRES_USER\" -d \"$TARGET\" --clean --if-exists --no-owner --exit-on-error" < "$DUMP"
else
  if [[ "$TARGET" != procurelens_restore* ]]; then
    echo "Isolated restore targets must start with 'procurelens_restore'." >&2; exit 1
  fi
  docker compose exec -T db sh -c "dropdb -U \"\$POSTGRES_USER\" --if-exists \"$TARGET\" && createdb -U \"\$POSTGRES_USER\" \"$TARGET\""
  docker compose exec -T db sh -c "pg_restore -U \"\$POSTGRES_USER\" -d \"$TARGET\" --no-owner --exit-on-error" < "$DUMP"
fi
echo "Row counts in '$TARGET':"
docker compose exec -T db sh -c "psql -U \"\$POSTGRES_USER\" -d \"$TARGET\" -At -F '=' -v ON_ERROR_STOP=1" < scripts/verify-counts.sql
