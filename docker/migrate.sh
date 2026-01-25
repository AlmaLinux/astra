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

echo "[entrypoint] Running migrations (with retry)..."
for i in $(seq 1 "${DJANGO_MIGRATE_RETRIES:-10}"); do
  if python manage.py createcachetable; then
    break
  fi
  echo "[entrypoint] createcachetable failed; retry ${i}/${DJANGO_MIGRATE_RETRIES:-10} in 2s"
  sleep 2
done
if (( i == ${DJANGO_MIGRATE_RETRIES:-10} )); then
  echo "[entrypoint] createcachetable failed after ${DJANGO_MIGRATE_RETRIES:-10} attempts" >&2
  exit 1
fi

echo "[entrypoint] Running migrations (with retry)..."
for i in $(seq 1 "${DJANGO_MIGRATE_RETRIES:-10}"); do
  if python manage.py migrate --noinput; then
    break
  fi
  echo "[entrypoint] migrate failed; retry ${i}/${DJANGO_MIGRATE_RETRIES:-10} in 2s"
  sleep 2
done
if (( i == ${DJANGO_MIGRATE_RETRIES:-10} )); then
  echo "[entrypoint] migrate failed after ${DJANGO_MIGRATE_RETRIES:-10} attempts" >&2
  exit 1
fi
