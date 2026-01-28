#!/usr/bin/env bash
set -euo pipefail

# The Docker image layout is /app/astra_app/<repo>, so manage.py lives under
# /app/astra_app/astra_app/manage.py.
if [[ -f "/app/astra_app/astra_app/manage.py" ]]; then
  cd /app/astra_app/astra_app/
elif [[ -f "/app/astra_app/manage.py" ]]; then
  cd /app/astra_app/
else
  echo "[entrypoint] Could not find manage.py under /app/astra_app" >&2
  exit 1
fi

if [[ "${DJANGO_AUTO_MIGRATE:-0}" == "1" ]]; then
  pwd
  ls -lR
  migrate.sh
fi

exec "$@"
