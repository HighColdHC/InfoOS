#!/usr/bin/env bash
set -euo pipefail

[[ "$(id -u)" == "0" ]] || {
  echo "install-systemd.sh must run as root" >&2
  exit 1
}

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${INFOOS_APP_DIR:-/opt/infoos/app}"
RUNTIME_USER="${INFOOS_RUNTIME_USER:-infoos}"
RUNTIME_GROUP="${INFOOS_RUNTIME_GROUP:-infoos}"

if ! getent group "$RUNTIME_GROUP" >/dev/null; then
  groupadd --system "$RUNTIME_GROUP"
fi
if ! id "$RUNTIME_USER" >/dev/null 2>&1; then
  useradd --system --gid "$RUNTIME_GROUP" --home-dir /var/lib/infoos/state \
    --create-home --shell /bin/bash "$RUNTIME_USER"
fi

install -d -o "$RUNTIME_USER" -g "$RUNTIME_GROUP" -m 0755 /opt/infoos
install -d -o "$RUNTIME_USER" -g "$RUNTIME_GROUP" -m 0755 "$APP_DIR"
install -d -o "$RUNTIME_USER" -g "$RUNTIME_GROUP" -m 0700 \
  /var/lib/infoos/state /var/lib/infoos/state/config \
  /var/lib/infoos/state/chrome-profile /var/lib/infoos/data
install -d -o root -g "$RUNTIME_GROUP" -m 0750 /etc/infoos

if [[ ! -f /etc/infoos/infoos.env ]]; then
  install -o root -g "$RUNTIME_GROUP" -m 0640 \
    "$DEPLOY_DIR/infoos.env.example" /etc/infoos/infoos.env
fi

for unit in "$DEPLOY_DIR"/systemd/*; do
  unit_target="/etc/systemd/system/$(basename "$unit")"
  sed \
    -e "s/^User=infoos$/User=$RUNTIME_USER/" \
    -e "s/^Group=infoos$/Group=$RUNTIME_GROUP/" \
    "$unit" > "$unit_target"
  chown root:root "$unit_target"
  chmod 0644 "$unit_target"
done

chown root:"$RUNTIME_GROUP" /etc/infoos/infoos.env
chmod 0640 /etc/infoos/infoos.env

systemctl daemon-reload
systemctl enable infoos-web.service

echo "InfoOS systemd units installed. Review /etc/infoos/infoos.env, then run:"
echo "  systemctl restart infoos-web"
