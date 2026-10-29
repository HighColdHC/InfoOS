#!/bin/sh
set -eu

revision="${1:-}"
case "${revision}" in
  ""|unknown|UNKNOWN|Unknown)
    echo "INFOOS_SOURCE_COMMIT must identify the exact source revision" >&2
    exit 64
    ;;
  *[!0-9a-fA-F]*)
    echo "INFOOS_SOURCE_COMMIT must be a full hexadecimal Git object ID" >&2
    exit 64
    ;;
esac

if [ "${#revision}" -ne 40 ] && [ "${#revision}" -ne 64 ]; then
  echo "INFOOS_SOURCE_COMMIT must be a full 40- or 64-character Git object ID" >&2
  exit 64
fi

case "${revision}" in
  0000000000000000000000000000000000000000|\
  0000000000000000000000000000000000000000000000000000000000000000)
    echo "INFOOS_SOURCE_COMMIT cannot be the all-zero object ID" >&2
    exit 64
    ;;
esac

printf '%s\n' "${revision}"
