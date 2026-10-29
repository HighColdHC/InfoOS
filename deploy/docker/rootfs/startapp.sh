#!/bin/sh
set -eu

profile_dir="${INFOOS_CHROME_PROFILE_DIR:-/var/lib/infoos/state/chrome-profile}"
start_url="${INFOOS_CHROME_START_URL:-https://x.com/home}"
browser_proxy_url="${INFOOS_BROWSER_PROXY_URL:-}"
embedded_proxy_enabled="${INFOOS_EMBEDDED_PROXY_ENABLED:-0}"
embedded_proxy_control_port="${INFOOS_EMBEDDED_PROXY_CONTROL_PORT:-19090}"

mkdir -p "$profile_dir"
rm -f \
  "$profile_dir/SingletonCookie" \
  "$profile_dir/SingletonLock" \
  "$profile_dir/SingletonSocket"

case "$browser_proxy_url" in
  "")
    ;;
  *"@"*)
    echo "INFOOS_BROWSER_PROXY_URL must not contain credentials" >&2
    exit 2
    ;;
  http://*|https://*|socks4://*|socks5://*)
    ;;
  *)
    echo "INFOOS_BROWSER_PROXY_URL uses an unsupported proxy scheme" >&2
    exit 2
    ;;
esac

if [ "$embedded_proxy_enabled" = "1" ]; then
  waited=0
  until curl --fail --silent --noproxy '*' \
    "http://127.0.0.1:${embedded_proxy_control_port}/version" >/dev/null; do
    [ "$waited" -lt 30 ] || {
      echo "embedded proxy did not become ready before Chrome startup" >&2
      exit 2
    }
    sleep 1
    waited="$((waited + 1))"
  done
fi

set -- /usr/bin/google-chrome-stable \
  --user-data-dir="$profile_dir" \
  --profile-directory=Default \
  --no-first-run \
  --no-default-browser-check
if [ -n "$browser_proxy_url" ]; then
  set -- "$@" "--proxy-server=$browser_proxy_url"
fi
set -- "$@" "$start_url"

exec /opt/infoos/bin/infoos-run-service chrome "$@"
