#!/usr/bin/env bash
set -euo pipefail

[[ "$(id -u)" == "0" ]] || {
  echo "install-host-monitor.sh must run as root" >&2
  exit 1
}

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$DEPLOY_DIR/../.." && pwd)"
STATE_DIR="${INFOOS_HOST_MONITOR_STATE_DIR:-/var/lib/infoos-host-monitor}"
LOG_DIR="${INFOOS_HOST_MONITOR_LOG_DIR:-/var/log/infoos-hardware-monitor}"

install -d -o root -g root -m 0700 "$STATE_DIR" "$STATE_DIR/incidents" "$STATE_DIR/boots"
install -d -o root -g adm -m 0750 "$LOG_DIR"
install -o root -g root -m 0755 \
  "$REPO_DIR/scripts/infoos-host-monitor.py" /usr/local/sbin/infoos-host-monitor
install -o root -g root -m 0644 \
  "$REPO_DIR/scripts/infoos_host_monitor_lib.py" /usr/local/sbin/infoos_host_monitor_lib.py
install -o root -g root -m 0644 \
  "$REPO_DIR/scripts/infoos_host_monitor_incidents.py" /usr/local/sbin/infoos_host_monitor_incidents.py
install -o root -g root -m 0644 \
  "$DEPLOY_DIR/systemd/infoos-host-forensics.service" \
  /etc/systemd/system/infoos-host-forensics.service
install -o root -g root -m 0644 \
  "$DEPLOY_DIR/systemd/infoos-host-monitor.service" \
  /etc/systemd/system/infoos-host-monitor.service

install -d -o root -g root -m 0755 /etc/systemd/journald.conf.d
install -o root -g root -m 0644 \
  "$DEPLOY_DIR/journald/99-infoos-forensics.conf" \
  /etc/systemd/journald.conf.d/99-infoos-forensics.conf

install -d -o root -g root -m 0755 /etc/modules-load.d
install -o root -g root -m 0644 \
  "$DEPLOY_DIR/modules-load.d/infoos-forensics.conf" \
  /etc/modules-load.d/infoos-forensics.conf

if [[ -d /etc/tlp.d ]]; then
  install -o root -g root -m 0644 \
    "$DEPLOY_DIR/tlp/99-infoos-forensics.conf" \
    /etc/tlp.d/99-infoos-forensics.conf
fi

modprobe efi_pstore 2>/dev/null || true
systemctl daemon-reload
systemctl restart systemd-journald.service
journalctl --flush
if command -v tlp >/dev/null 2>&1; then
  tlp start
fi
sysctl -w kernel.nmi_watchdog=1
systemctl enable infoos-host-forensics.service infoos-host-monitor.service
systemctl restart infoos-host-forensics.service
systemctl restart infoos-host-monitor.service
systemctl --quiet is-active infoos-host-forensics.service
systemctl --quiet is-active infoos-host-monitor.service

echo "InfoOS host monitor installed."
echo "  status:    infoos-host-monitor status"
echo "  incidents: infoos-host-monitor incidents"
echo "  collect:   infoos-host-monitor collect --reason manual"
