#!/bin/bash
# Linux systemd user installer. No root, daemon-wide changes or credential reads.
set -euo pipefail
umask 077
source "$(dirname -- "${BASH_SOURCE[0]}")/install-knowledge-common.sh"
if [ "$(uname -s)" != Linux ]; then echo 'This installer requires Linux' >&2; exit 2; fi
if [ "$DISABLE" -eq 1 ]; then
  systemctl --user disable --now infoos-knowledge.timer
  systemctl --user stop infoos-knowledge.service
  exit 0
fi
install_knowledge_snapshot
if [ "$COPY_ONLY" -eq 1 ]; then exit 0; fi
mkdir -p "$INFOOS_SYSTEMD_USER_DIR"
python3 - "$SCRIPT_DIR/systemd" <<'PY'
import os
from pathlib import Path
import sys

install_dir = os.environ["INFOOS_KNOWLEDGE_INSTALL_DIR"]
# Quote unit values and escape systemd specifiers (ExecStart ':' disables $ expansion).
def quote(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'

values = {
    "INSTALL_DIR": quote(install_dir),
    "CONFIG_ENV": quote(f"INFOOS_AGENT_CONFIG_FILE={install_dir}/installed.env"),
    "LAUNCHER": quote(f"{install_dir}/run-knowledge.sh"),
    "TIMEOUT": os.environ["INFOOS_KNOWLEDGE_TIMEOUT_SEC"],
    "HOURS": ",".join(os.environ["INFOOS_KNOWLEDGE_HOURS"].split()),
    "MINUTE": os.environ["INFOOS_KNOWLEDGE_MINUTE"],
    "TIMEZONE": os.environ["INFOOS_KNOWLEDGE_TIMEZONE"],
}
for name in ("infoos-knowledge.service", "infoos-knowledge.timer"):
    text = (Path(sys.argv[1]) / f"{name}.in").read_text()
    for key, value in values.items():
        text = text.replace(f"@{key}@", value)
    target = Path(os.environ["INFOOS_SYSTEMD_USER_DIR"]) / name
    target.write_text(text)
    target.chmod(0o600)
PY
# Explicit paths also support a configured directory outside the default search path.
systemctl --user link "$INFOOS_SYSTEMD_USER_DIR/infoos-knowledge.service" "$INFOOS_SYSTEMD_USER_DIR/infoos-knowledge.timer"
systemctl --user daemon-reload
systemctl --user enable --now infoos-knowledge.timer
