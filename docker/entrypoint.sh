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
  echo "[entrypoint] Running migrations (with retry)..."
  for i in $(seq 1 "${DJANGO_MIGRATE_RETRIES:-30}"); do
    if python manage.py createcachetable && python manage.py migrate --noinput; then
      break
    fi
    echo "[entrypoint] createcachetable/migrate failed; retry ${i}/${DJANGO_MIGRATE_RETRIES:-30} in 2s"
    sleep 2
  done
fi

# Start health check server on port 9000 in background
echo "[entrypoint] Starting health check server on port 9000..."
python healthcheck_server.py &

exec "$@"
