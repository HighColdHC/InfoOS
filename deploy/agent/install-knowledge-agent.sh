#!/bin/bash
# macOS launchd installer; --copy-only never calls launchctl.
set -euo pipefail
umask 077
source "$(dirname -- "${BASH_SOURCE[0]}")/install-knowledge-common.sh"
if [ "$(uname -s)" != Darwin ]; then echo 'Use install-knowledge-agent-systemd.sh on Linux' >&2; exit 2; fi
DOMAIN="gui/$(id -u)"
if [ "$DISABLE" -eq 1 ]; then
  launchctl bootout "$DOMAIN/com.infoos.knowledge" 2>/dev/null || :
  launchctl disable "$DOMAIN/com.infoos.knowledge"
  exit 0
fi
install_knowledge_snapshot
if [ "$COPY_ONLY" -eq 1 ]; then exit 0; fi
PLIST="$INFOOS_LAUNCHD_DIR/com.infoos.knowledge.plist"
mkdir -p "$INFOOS_LAUNCHD_DIR"
python3 - "$PLIST" <<'PY'
import os
import plistlib
import sys

install_dir = os.environ["INFOOS_KNOWLEDGE_INSTALL_DIR"]
log_dir = os.environ["INFOOS_KNOWLEDGE_LOG_DIR"]
with open(sys.argv[1], "wb") as stream:
    plistlib.dump({
        "Label": "com.infoos.knowledge",
        "ProgramArguments": ["/bin/bash", f"{install_dir}/run-knowledge.sh"],
        "WorkingDirectory": install_dir,
        "EnvironmentVariables": {"INFOOS_AGENT_CONFIG_FILE": f"{install_dir}/installed.env"},
        # Minute ticks plus timezone filtering also support fractional UTC offsets.
        "StartCalendarInterval": [{"Minute": minute} for minute in range(60)],
        "StandardOutPath": f"{log_dir}/launchd.out",
        "StandardErrorPath": f"{log_dir}/launchd.err",
    }, stream, sort_keys=False)
PY
chmod 0600 "$PLIST"
# Preserve the installed legacy plists for an explicit rollback.
for label in com.infoos.evaluate com.infoos.opportunities; do
  launchctl bootout "$DOMAIN/$label" 2>/dev/null || :
  launchctl disable "$DOMAIN/$label"
done
launchctl bootout "$DOMAIN/com.infoos.knowledge" 2>/dev/null || :
launchctl enable "$DOMAIN/com.infoos.knowledge"
launchctl bootstrap "$DOMAIN" "$PLIST"
