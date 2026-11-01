#!/usr/bin/env bash
# Logical backup of the ProcureLens database (custom format) to ./backups/.
set -euo pipefail
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1
mkdir -p backups
OUT="backups/procurelens-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$OUT"
[[ -s "$OUT" ]] || { echo "Backup failed (empty file)." >&2; rm -f "$OUT"; exit 1; }
chmod 600 "$OUT" 2>/dev/null || true
echo "Backup written: $OUT ($(wc -c < "$OUT") bytes). It contains application data and password hashes — store it privately."
