#!/bin/sh
set -eu

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
project_root="$(CDPATH= cd -- "${script_dir}/../.." && pwd)"
env_file="${INFOOS_DOCKER_ENV_FILE:-${script_dir}/.env}"

if [ ! -f "${env_file}" ]; then
  echo "missing Docker Appliance environment file: ${env_file}" >&2
  exit 66
fi
if ! git -C "${project_root}" diff --quiet --ignore-submodules -- ||
   ! git -C "${project_root}" diff --cached --quiet --ignore-submodules -- ||
   [ -n "$(git -C "${project_root}" ls-files --others --exclude-standard)" ]; then
  echo "refusing release build from a dirty worktree" >&2
  exit 65
fi

revision="$(git -C "${project_root}" rev-parse --verify 'HEAD^{commit}')"
canonical_branch="${INFOOS_CANONICAL_BRANCH:-}"
if [ -n "${canonical_branch}" ] &&
   [ "$(git -C "${project_root}" branch --show-current)" != "${canonical_branch}" ]; then
  echo "release images must be built from ${canonical_branch}" >&2
  exit 65
fi
"${script_dir}/scripts/validate-source-revision.sh" "${revision}" >/dev/null
export INFOOS_SOURCE_COMMIT="${revision}"
export DOCKER_BUILDKIT="${DOCKER_BUILDKIT:-1}"
export DOCKER_DEFAULT_PLATFORM="${DOCKER_DEFAULT_PLATFORM:-linux/amd64}"

set -- -f "${script_dir}/compose.yaml" "$@"
exec docker compose --env-file "${env_file}" "$@" build
