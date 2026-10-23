#!/usr/bin/env bash
set -euo pipefail

: "${RANGER_ADMIN_PASSWORD:?RANGER_ADMIN_PASSWORD must be configured}"
startup=$(mktemp)
trap 'rm -f "$startup"' EXIT

# The pinned image otherwise initializes the UI account with the DB credential.
python3 - "$startup" <<'PY'
import os
import sys
from pathlib import Path

original = Path(os.environ["RANGER_SCRIPTS"], "ranger.sh").read_text()
old = 'echo "rangerAdmin_password=${RANGER_DB_PASSWORD}"'
new = 'echo "rangerAdmin_password=${RANGER_ADMIN_PASSWORD}"'
if original.count(old) != 1:
    raise SystemExit("Ranger startup layout changed; review the pinned image adapter")
demo = 'python3 ${RANGER_SCRIPTS}/create-ranger-services.py'
if original.count(demo) != 1:
    raise SystemExit("Ranger service setup changed; review the pinned image adapter")
# Nova's ranger-bootstrap owns service provisioning, including its credentials.
Path(sys.argv[1]).write_text(original.replace(old, new).replace(demo, ':'))
PY

bash "$startup"
