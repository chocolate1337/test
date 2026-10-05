#!/bin/sh
set -e

python -m app.wait_for_deps

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  alembic upgrade head
fi

exec "$@"
