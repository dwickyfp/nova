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

# Only errors are logged, and Tomcat access logs are kept for two days instead
# of fifteen: every policy download and API call is one access-log line.
classes = Path(os.environ.get("RANGER_HOME", "/opt/ranger"), "admin/ews/webapp/WEB-INF/classes")
for conf in ("conf", "conf.dist"):
    logback = classes / conf / "logback.xml"
    if logback.exists():
        text = logback.read_text()
        for level in ("debug", "info", "warn"):
            text = text.replace(f'level="{level}"', 'level="error"')
            text = text.replace(f'<level value="{level}"', '<level value="error"')
        logback.write_text(text)
    site = classes / conf / "ranger-admin-site.xml"
    if site.exists() and "ranger.accesslog.rotate.max_days" not in site.read_text():
        text = site.read_text()
        if text.count("</configuration>") != 1:
            raise SystemExit("Ranger site layout changed; review the pinned image adapter")
        site.write_text(text.replace("</configuration>", (
            "  <property>\n    <name>ranger.accesslog.rotate.max_days</name>\n"
            "    <value>2</value>\n  </property>\n</configuration>")))
PY

bash "$startup"
