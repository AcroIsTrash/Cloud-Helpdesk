#!/usr/bin/env bash
# Build cloud-helpdesk:local inside a Claude Code cloud session.
#
# There, all HTTPS goes through a proxy with its own CA, and a plain
# `docker build` trusts neither, so `uv sync` fails inside the build. This
# builds a throwaway copy of the Dockerfile that mounts the proxy CA as a build
# secret for that one step; the real Dockerfile stays untouched (CI builds it
# as is). Afterwards: `docker compose up --no-build --wait`.
set -euo pipefail
cd "$(dirname "$0")/.."

ca=/root/.ccr/ca-bundle.crt
if [ ! -f "$ca" ] || [ -z "${HTTPS_PROXY:-}" ]; then
  echo "no agent proxy here: use a plain 'docker compose build'" >&2
  exit 1
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
sed 's|^RUN uv sync |RUN --mount=type=secret,id=ca,target=/ca.crt SSL_CERT_FILE=/ca.crt uv sync |' \
  Dockerfile > "$tmp/Dockerfile"
if cmp -s Dockerfile "$tmp/Dockerfile"; then
  echo "the Dockerfile has no 'RUN uv sync' line to patch; update this script" >&2
  exit 1
fi

docker build --network=host \
  --build-arg HTTPS_PROXY="$HTTPS_PROXY" \
  --secret id=ca,src="$ca" \
  -f "$tmp/Dockerfile" -t cloud-helpdesk:local "$@" .
