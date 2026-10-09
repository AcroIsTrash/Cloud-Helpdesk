#!/bin/bash
# Cloud-session setup: dependencies from uv.lock, a local Postgres for the tests
# and the app, and a running Docker daemon.
#
# Why a local Postgres: the tests normally start one in Docker (testcontainers),
# but Docker Hub can rate-limit image pulls from this container's shared IP
# (429). The image ships Postgres 16, so the hook starts that and points the
# tests at it through TEST_DATABASE_URL. Your machine and CI still use Docker.
# Known gap: no pgvector here (needed from Phase 4).
#
# Docker: the daemon isn't started by default. Image builds need the proxy CA:
# use scripts/sandbox-build.sh, then `docker compose up --no-build --wait`.
# ghcr.io image downloads are refused by the egress proxy; Docker Hub works.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# UV_NATIVE_TLS is deprecated; UV_SYSTEM_CERTS is its replacement.
unset UV_NATIVE_TLS
export UV_SYSTEM_CERTS=true
uv sync --locked

pg_ctlcluster 16 main start >/dev/null 2>&1 || true  # already running is fine
for _ in $(seq 1 30); do
  pg_isready -q -h localhost -p 5432 && break
  sleep 1
done

psql_admin() { su postgres -c "psql -q -v ON_ERROR_STOP=1 $*"; }
psql_admin "-c \"ALTER USER postgres PASSWORD 'postgres'\""
for db in helpdesk helpdesk_test; do
  if ! su postgres -c "psql -tAc \"SELECT 1 FROM pg_database WHERE datname = '$db'\"" | grep -q 1; then
    psql_admin "-c \"CREATE DATABASE $db\""
  fi
done

if command -v dockerd >/dev/null && ! docker info >/dev/null 2>&1; then
  (dockerd >/var/log/dockerd.log 2>&1 &)
  for _ in $(seq 1 30); do
    docker info >/dev/null 2>&1 && break
    sleep 1
  done
fi

# The app (DATABASE_URL) and the tests (TEST_DATABASE_URL) get separate
# databases: the tests truncate every table.
{
  echo 'unset UV_NATIVE_TLS'
  echo 'export UV_SYSTEM_CERTS=true'
  echo 'export DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/helpdesk'
  echo 'export TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/helpdesk_test'
} >> "${CLAUDE_ENV_FILE:-/dev/null}"
