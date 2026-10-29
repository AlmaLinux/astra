#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="/etc/astra/astra.env"
LAST_IMAGE_FILE="/etc/astra/last_app_image"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a

if [[ -z "${APP_IMAGE:-}" ]]; then
  echo "APP_IMAGE is not set in $ENV_FILE" >&2
  exit 1
fi

wait_for_unit() {
  local unit="$1"
  local timeout="${2:-300}"
  local elapsed=0

  while ! systemctl is-active --quiet "$unit"; do
    if (( elapsed >= timeout )); then
      systemctl status "$unit" --no-pager
      echo "Timed out waiting for $unit" >&2
      exit 1
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
}

# Django rejects Hosts outside ALLOWED_HOSTS, so probe with the public hostname.
probe_host="${PUBLIC_BASE_URL:-}"
probe_host="${probe_host#*://}"
probe_host="${probe_host%%/*}"
probe_host="${probe_host:-localhost}"

# systemd reports the unit active as soon as the gunicorn arbiter is up, which
# is before the workers have loaded Django. Wait until the instance actually
# answers before taking the other one down.
wait_until_serving() {
  local port="$1"
  local timeout="${2:-300}"
  local elapsed=0

  until curl -fs -o /dev/null --max-time 5 \
      -H "Host: ${probe_host}" -H "X-Forwarded-Proto: https" \
      "http://127.0.0.1:${port}/readyz"; do
    if (( elapsed >= timeout )); then
      echo "Timed out waiting for 127.0.0.1:${port}/readyz" >&2
      exit 1
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
}

restart_instance() {
  local i="$1"
  systemctl restart "astra-app@${i}.service"
  wait_for_unit "astra-app@${i}.service"
  wait_until_serving "800${i}"
}

current_digest=""
current_image_id=""
for container_name in astra-app-1 astra-app-2; do
  if podman inspect "$container_name" >/dev/null 2>&1; then
    current_image_id=$(podman inspect "$container_name" --format '{{.Image}}')
    if [[ -n "$current_image_id" ]]; then
      break
    fi
  fi
done

if [[ -n "$current_image_id" ]]; then
  current_digest=$(podman image inspect "$current_image_id" --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}')
elif podman image inspect "$APP_IMAGE" >/dev/null 2>&1; then
  current_digest=$(podman image inspect "$APP_IMAGE" --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}')
fi
if [[ -n "$current_digest" ]]; then
  echo "$current_digest" > "$LAST_IMAGE_FILE"
fi

podman pull "$APP_IMAGE"

migration_container="astra-migrate-$(date +%s)"
if ! podman run --rm --name "$migration_container" --env-file "$ENV_FILE" "$APP_IMAGE" migrate.sh; then
  echo "Migration failed; containers were not restarted." >&2
  exit 1
fi

restart_instance 1
# Give Caddy's passive health check (fail_duration 5s) time to forget
# instance 1's failures before instance 2 goes down.
sleep 10
restart_instance 2
