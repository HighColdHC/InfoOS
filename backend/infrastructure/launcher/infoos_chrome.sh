#!/usr/bin/env bash
# Compatibility entrypoint. The audited cross-platform implementation lives in
# scripts/opencli-infoos-chrome so installed and source-tree launches share one
# browser-session.json / flock / infoos-fetch binding contract.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
LAUNCHER="$REPO_ROOT/scripts/opencli-infoos-chrome"

[[ -x "$LAUNCHER" ]] || {
  printf 'InfoOS browser launcher is missing or not executable: %s\n' "$LAUNCHER" >&2
  exit 1
}

# The delegated launcher owns --headless handling, OpenCLI profile rename/use,
# exact Chrome executable/profile/UID matching, and the browser-session lock.
exec "$LAUNCHER" "$@"
