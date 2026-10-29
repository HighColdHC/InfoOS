#!/bin/sh
# Replaces the base-image initializer at the exact same cont-init path.
set -eu

case "${WEB_AUTHENTICATION:-0}" in 1|true|TRUE|yes|YES) ;; *) exit 0 ;; esac
case "${SECURE_CONNECTION:-0}" in
  1|true|TRUE|yes|YES) ;;
  *) echo 'ERROR: web authentication requires SECURE_CONNECTION=1.' >&2; exit 1 ;;
esac

password_file="${INFOOS_WEBAUTH_PASSWORD_FILE:-/config/webauth-htpasswd}"
umask 077
# A separate inode remains locked across the atomic rename.
exec 9>"${password_file}.lock"
flock -x 9
if [ -f "$password_file" ] && awk -F: '!/^#/ && NF >= 2 && length($1) && length($2) {found=1} END {exit !found}' "$password_file"; then
  echo 'Web authentication database exists; bootstrap environment ignored.'
  exit 0
fi
if [ -z "${WEB_AUTHENTICATION_USERNAME:-}" ] || [ -z "${WEB_AUTHENTICATION_PASSWORD:-}" ]; then
  echo 'ERROR: empty web authentication database; set WEB_AUTHENTICATION_USERNAME and WEB_AUTHENTICATION_PASSWORD for first initialization.' >&2
  exit 1
fi
# Do not let htpasswd diagnostics print the account name or other supplied data.
if ! printf '%s' "$WEB_AUTHENTICATION_USERNAME" | LC_ALL=C grep -Eq '^[A-Za-z0-9@._+-]{3,128}$'; then
  echo 'ERROR: invalid initial account name.' >&2; exit 1
fi
# htpasswd reads one line and bcrypt only accepts 72 bytes. Reject truncation.
password_size="$(printf '%s' "$WEB_AUTHENTICATION_PASSWORD" | LC_ALL=C wc -c)"
password_lines="$(printf '%s' "$WEB_AUTHENTICATION_PASSWORD" | tr -d '\r\n' | LC_ALL=C wc -c)"
if [ "$password_size" -gt 72 ] || [ "$password_size" -ne "$password_lines" ]; then
  echo 'ERROR: initial password must be at most 72 bytes without line breaks.' >&2; exit 1
fi
temporary="$(mktemp "${password_file}.init.XXXXXX")"
trap 'rm -f "$temporary"' EXIT HUP INT TERM
if ! printf '%s\n' "$WEB_AUTHENTICATION_PASSWORD" | htpasswd -B -C 12 -i "$temporary" "$WEB_AUTHENTICATION_USERNAME" >/dev/null 2>&1; then
  echo 'ERROR: web authentication initialization failed.' >&2; exit 1
fi
chmod 0600 "$temporary"
chown "${USER_ID:-1000}:${GROUP_ID:-1000}" "$temporary" "${password_file}.lock"
mv -f "$temporary" "$password_file"
echo 'Web authentication database initialized with bcrypt.'
