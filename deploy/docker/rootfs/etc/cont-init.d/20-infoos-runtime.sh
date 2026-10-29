#!/bin/sh
set -eu

runtime_uid="${USER_ID:-1000}"
runtime_gid="${GROUP_ID:-1000}"

/opt/infoos/bin/infoos-verify-runtime-capabilities

mkdir -p \
  /var/lib/infoos/state/chrome-profile \
  /var/lib/infoos/state/config \
  /var/lib/infoos/state/lark-cli/config \
  /var/lib/infoos/state/lark-cli/data \
  /var/lib/infoos/state/lark-cli/logs \
  /var/lib/infoos/state/lark-cli/setup \
  /var/lib/infoos/state/logs/opencli \
  /var/lib/infoos/state/proxy \
  /var/lib/infoos/data/assets \
  /var/lib/infoos/data/exports \
  /var/lib/infoos/data/logs

chown -R "$runtime_uid:$runtime_gid" /var/lib/infoos/state /var/lib/infoos/data
chmod 0700 /var/lib/infoos/state/chrome-profile
chmod 0700 /var/lib/infoos/state/proxy
chmod 0700 /var/lib/infoos/state/lark-cli /var/lib/infoos/state/lark-cli/config \
  /var/lib/infoos/state/lark-cli/data /var/lib/infoos/state/lark-cli/logs /var/lib/infoos/state/lark-cli/setup

embedded_proxy_enabled="${INFOOS_EMBEDDED_PROXY_ENABLED:-0}"
embedded_proxy_url="http://127.0.0.1:${INFOOS_EMBEDDED_PROXY_MIXED_PORT:-17897}"
case "$embedded_proxy_enabled" in
  0)
    ;;
  1)
    proxy_config="${INFOOS_EMBEDDED_PROXY_CONFIG:-/var/lib/infoos/state/proxy/source.yaml}"
    [ -s "$proxy_config" ] || {
      echo "embedded proxy is enabled but its source config is missing or empty" >&2
      exit 2
    }
    chmod 0600 "$proxy_config"
    for variable_name in HTTP_PROXY HTTPS_PROXY http_proxy https_proxy INFOOS_BROWSER_PROXY_URL; do
      eval "variable_value=\${$variable_name:-}"
      [ "$variable_value" = "$embedded_proxy_url" ] || {
        echo "$variable_name must equal the embedded proxy loopback URL when INFOOS_EMBEDDED_PROXY_ENABLED=1" >&2
        exit 2
      }
    done
    ;;
  *)
    echo "INFOOS_EMBEDDED_PROXY_ENABLED must be 0 or 1" >&2
    exit 2
    ;;
esac

rm -f \
  /tmp/infoos-draining \
  /tmp/infoos-chrome-stopped \
  /tmp/infoos-infoos-web-stopped \
  /tmp/infoos-infoos-worker-public-fetch-stopped \
  /tmp/infoos-infoos-worker-browser-fetch-stopped \
  /tmp/infoos-infoos-worker-urgent-media-stopped \
  /tmp/infoos-infoos-worker-card-pipeline-stopped \
  /tmp/infoos-infoos-worker-gpu-asr-stopped \
  /tmp/infoos-infoos-worker-maintenance-io-stopped \
  /tmp/infoos-infoos-scheduler-stopped \
  /tmp/infoos-infoos-proxy-stopped \
  /tmp/infoos-opencli-stopped \
  /tmp/infoos-health-status.json
