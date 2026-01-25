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

systemctl restart astra-app@1.service
wait_for_unit astra-app@1.service

systemctl restart astra-app@2.service
wait_for_unit astra-app@2.service
