#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="/etc/astra/astra.env"
LAST_IMAGE_FILE="/etc/astra/last_app_image"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE" >&2
  exit 1
fi

if [[ ! -f "$LAST_IMAGE_FILE" ]]; then
  echo "Missing rollback image file $LAST_IMAGE_FILE" >&2
  exit 1
fi

rollback_image=$(cat "$LAST_IMAGE_FILE")
if [[ -z "$rollback_image" ]]; then
  echo "Rollback image is empty" >&2
  exit 1
fi

set_env_value() {
  local key="$1"
  local value="$2"
  local tmp_file

  tmp_file="$(mktemp)"
  awk -v key="$key" -v value="$value" '
    BEGIN { found = 0 }
    $0 ~ "^" key "=" {
      print key "=" value
      found = 1
      next
    }
    { print }
    END {
      if (found == 0) {
        print key "=" value
      }
    }
  ' "$ENV_FILE" > "$tmp_file"
  mv "$tmp_file" "$ENV_FILE"
}

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

set_env_value "APP_IMAGE" "$rollback_image"

set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a

# Django rejects Hosts outside ALLOWED_HOSTS, so probe with the public hostname.
probe_host="${PUBLIC_BASE_URL:-}"
probe_host="${probe_host#*://}"
probe_host="${probe_host%%/*}"
probe_host="${probe_host:-localhost}"

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

podman pull "$rollback_image"

restart_instance 1
# Give Caddy's passive health check (fail_duration 5s) time to forget
# instance 1's failures before instance 2 goes down.
sleep 10
restart_instance 2
