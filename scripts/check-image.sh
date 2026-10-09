#!/usr/bin/env bash
# Check what's inside the image, not what the Dockerfile meant to put there:
# only the app, its migrations and runtime dependencies, run as non-root.
#
#   scripts/check-image.sh [image]        (default: cloud-helpdesk:local)
set -euo pipefail
image="${1:-cloud-helpdesk:local}"
fail=0

run() { docker run --rm --entrypoint "" "$image" "$@"; }

echo "== /app"
run ls -A /app
unexpected=$(run ls -A /app | grep -vxE '\.venv|alembic\.ini|app|migrations' || true)
if [ -n "$unexpected" ]; then
  echo "FAIL: unexpected files in /app: $unexpected"; fail=1
fi

tests=$(run find / -xdev \( -path /proc -o -path /app/.venv \) -prune -o \
  \( -name 'test_*.py' -o -name conftest.py -o -name evals \) -print 2>/dev/null \
  | grep -v '^/usr/local/lib/python' || true)
if [ -n "$tests" ]; then
  echo "FAIL: test or eval files in the image: $tests"; fail=1
fi

echo "== installed packages"
packages=$(run python -c 'import importlib.metadata as m; print("\n".join(sorted(d.metadata["Name"].lower() for d in m.distributions())))')
echo "$packages" | paste -sd' '
for dev in pytest ruff mypy testcontainers httpx2; do
  if echo "$packages" | grep -qx "$dev"; then
    echo "FAIL: dev dependency $dev is installed"; fail=1
  fi
done

echo "== user"
uid=$(docker run --rm "$image" id -u)
echo "uid $uid"
if [ "$uid" = "0" ]; then
  echo "FAIL: the image runs as root"; fail=1
fi

[ "$fail" = 0 ] && echo "OK: $image holds only the app and runs as non-root"
exit "$fail"
