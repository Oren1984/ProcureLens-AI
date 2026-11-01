#!/usr/bin/env bash
# Verify that 127.0.0.1:8089 is free before startup. Never stops other applications and never
# picks another port: if 8089 is taken by something other than this stack, it reports and exits.
set -euo pipefail
cd "$(dirname "$0")/.."
PORT=8089
if docker compose ps --status running --services 2>/dev/null | grep -qx app; then
  echo "Port ${PORT}: in use by this ProcureLens stack (app container running) — OK."
  exit 0
fi
PY=$(command -v python3 || command -v python)
if "$PY" - "$PORT" <<'PYEOF'
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)
finally:
    s.close()
PYEOF
then
  echo "Port ${PORT}: available."
else
  echo "CONFLICT: 127.0.0.1:${PORT} is already in use by another process." >&2
  echo "ProcureLens requires port ${PORT}. Free it yourself, then re-run; nothing was stopped." >&2
  exit 1
fi
