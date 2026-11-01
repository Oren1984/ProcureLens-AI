#!/usr/bin/env bash
# Port check, then build and start the default stack (app + PostgreSQL/pgvector).
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] || { echo "No .env — run ./scripts/init-env.sh first." >&2; exit 1; }
./scripts/check-port.sh
BUILD_ARGS=()
if [[ -n "${BUILD_CA_FILE:-}" ]]; then  # optional CA bundle for TLS-inspecting networks
  export BUILD_CA_FILE
  docker build --secret id=build_ca,src="$BUILD_CA_FILE" -t procurelens-app:1.0.0 .
else
  BUILD_ARGS+=(--build)
fi
docker compose up -d "${BUILD_ARGS[@]}"
echo "ProcureLens AI: http://localhost:8089"
