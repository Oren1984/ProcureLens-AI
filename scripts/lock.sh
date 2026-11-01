#!/usr/bin/env bash
# Regenerate hash-locked requirements for the Linux container (Python 3.12).
set -euo pipefail
cd "$(dirname "$0")/.."
COMMON=(--python-version 3.12 --python-platform x86_64-manylinux_2_28 --generate-hashes
        --override overrides.txt -q)
uv pip compile pyproject.toml "${COMMON[@]}" -o requirements.lock
uv pip compile pyproject.toml --extra dev "${COMMON[@]}" -o requirements-dev.lock
echo "Locked: requirements.lock, requirements-dev.lock"
