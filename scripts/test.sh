#!/usr/bin/env bash
# Run the test suite against a disposable PostgreSQL/pgvector container on 127.0.0.1:15432.
# Requires a dev environment: uv venv .venv && uv pip install -e ".[dev]"
set -euo pipefail
cd "$(dirname "$0")/.."
NAME=procurelens-testdb
PW=$(python -c 'import secrets; print(secrets.token_urlsafe(16))')
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" -e POSTGRES_USER=pl_owner -e POSTGRES_PASSWORD="$PW" \
  -e POSTGRES_DB=procurelens_test -p 127.0.0.1:15432:5432 pgvector/pgvector:0.8.2-pg17 >/dev/null
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT
for _ in $(seq 1 30); do
  docker exec "$NAME" pg_isready -U pl_owner -d procurelens_test >/dev/null 2>&1 && break; sleep 1
done
sleep 2
export TEST_DATABASE_URL="postgresql+psycopg://pl_owner:${PW}@127.0.0.1:15432/procurelens_test"
PY=.venv/bin/python; [[ -x $PY ]] || PY=.venv/Scripts/python.exe
"$PY" -m pytest "$@"
