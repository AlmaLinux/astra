#!/usr/bin/env bash
set -euo pipefail

if [[ $# -eq 0 ]]; then
  cat >&2 <<'EOF'
Usage: infra/scripts/astra-manage-web.sh <manage.py args...>

Examples:
  infra/scripts/astra-manage-web.sh help membership_request_repair
  infra/scripts/astra-manage-web.sh membership_request_repair --request-id 232 --reset-to-pending --dry-run
  infra/scripts/astra-manage-web.sh maintenance_shell
EOF
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

COMPOSE_CMD=(podman-compose)
if [[ -n "${ASTRA_COMPOSE_COMMAND:-}" ]]; then
  # Allow operators to swap the compose entry command without editing the script.
  read -r -a COMPOSE_CMD <<<"${ASTRA_COMPOSE_COMMAND}"
fi

exec "${COMPOSE_CMD[@]}" exec -T web python manage.py "$@"