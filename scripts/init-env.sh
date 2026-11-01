#!/usr/bin/env bash
# Create .env with freshly generated secrets (only if .env does not exist yet) and the
# private credentials directory outside the repository.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -f .env ]]; then
  echo ".env already exists — leaving it unchanged."
  exit 0
fi
PRIVATE_DIR="${1:-$HOME/.procurelens/private}"
case "$(realpath -m "$PRIVATE_DIR")" in
  "$(pwd)"*) echo "Refusing: the private directory must be outside the repository." >&2; exit 1;;
esac
mkdir -p "$PRIVATE_DIR"
chmod 700 "$PRIVATE_DIR" 2>/dev/null || true
gen() { python3 -c 'import secrets; print(secrets.token_urlsafe(32))' 2>/dev/null \
        || python -c 'import secrets; print(secrets.token_urlsafe(32))'; }
# Docker on Windows expects forward slashes in bind-mount paths.
PRIVATE_DIR_ESC="$(cd "$PRIVATE_DIR" && (pwd -W 2>/dev/null || pwd))"
sed -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(gen)|" \
    -e "s|^APP_DB_PASSWORD=.*|APP_DB_PASSWORD=$(gen)|" \
    -e "s|^PROCURELENS_PRIVATE_DIR=.*|PROCURELENS_PRIVATE_DIR=${PRIVATE_DIR_ESC}|" \
    .env.example > .env
chmod 600 .env 2>/dev/null || true
echo "Created .env (secrets generated, not printed). Private directory: ${PRIVATE_DIR_ESC}"
