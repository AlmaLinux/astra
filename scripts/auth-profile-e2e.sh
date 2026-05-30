#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE_PROJECT="${ASTRA_E2E_PROJECT:-astra-e2e}"
ENV_FILE="${ASTRA_E2E_ENV_FILE:-.env.e2e}"
READY_URL="${ASTRA_E2E_READY_URL:-http://127.0.0.1:18000/readyz}"
FRONTEND_DIR="${ASTRA_E2E_FRONTEND_DIR:-$REPO_ROOT/frontend}"
NPM_BIN="${ASTRA_E2E_NPM_BIN:-npm}"
CURL_BIN="${ASTRA_E2E_CURL_BIN:-curl}"
PODMAN_BIN="${ASTRA_E2E_PODMAN_BIN:-podman}"
BROWSER_CACHE_DIR="${ASTRA_E2E_BROWSER_CACHE_DIR:-${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}}"
BROWSER_SENTINEL_PATH="$BROWSER_CACHE_DIR/.astra-playwright-chromium-version"
SERVICES=(db minio minio_init web)

COMPOSE_CMD=(podman-compose)
if [[ -n "${ASTRA_E2E_COMPOSE_COMMAND:-}" ]]; then
  read -r -a COMPOSE_CMD <<<"${ASTRA_E2E_COMPOSE_COMMAND}"
fi
COMPOSE_CMD+=(
  -p "$COMPOSE_PROJECT"
  --env-file "$ENV_FILE"
  -f docker-compose.yml
  -f docker-compose.e2e.yml
)

usage() {
  cat <<'EOF'
Usage: scripts/auth-profile-e2e.sh [run] [--no-reset] [--headed] [--ui]
       scripts/auth-profile-e2e.sh up
       scripts/auth-profile-e2e.sh reset
       scripts/auth-profile-e2e.sh install
       scripts/auth-profile-e2e.sh down
       scripts/auth-profile-e2e.sh help

Commands:
  run      Start or reuse the E2E stack, wait for readiness, reset auth/profile state,
           and run the checked-in Playwright auth/profile spec. This is the default.
  up       Start or reuse the E2E stack and wait for /readyz.
  reset    Start or reuse the E2E stack, wait for /readyz, and run auth_profile_reset.
  install  Ensure frontend dependencies and the Chromium browser are installed.
  down     Stop the isolated astra-e2e stack.
  help     Show this help output.

Options:
  --no-reset  Skip auth_profile_reset for the default run command.
  --headed    Forward Playwright's --headed flag for the default run command.
  --ui        Forward Playwright's --ui flag for the default run command.

Environment overrides:
  ASTRA_E2E_COMPOSE_COMMAND  Override the compose entry command.
  ASTRA_E2E_PROJECT          Override the compose project name. Default: astra-e2e.
  ASTRA_E2E_ENV_FILE         Override the env file. Default: .env.e2e.
  ASTRA_E2E_READY_URL        Override the readiness URL. Default: http://127.0.0.1:18000/readyz.
  ASTRA_E2E_FRONTEND_DIR     Override the frontend directory. Default: ./frontend.
EOF
}

compose() {
  "${COMPOSE_CMD[@]}" "$@"
}

stack_running() {
  local web_id
  web_id="$(
    "$PODMAN_BIN" ps \
      --filter "label=io.podman.compose.project=$COMPOSE_PROJECT" \
      --filter "label=io.podman.compose.service=web" \
      --format '{{.ID}}' | tr -d '[:space:]'
  )"
  [[ -n "$web_id" ]]
}

wait_until_ready() {
  "$CURL_BIN" --retry 30 --retry-all-errors --retry-delay 2 -fsS "$READY_URL" >/dev/null
}

ensure_stack() {
  if stack_running; then
    echo "Reusing astra-e2e stack"
  else
    echo "Starting astra-e2e stack"
    compose up -d "${SERVICES[@]}"
  fi
  wait_until_ready
}

ensure_frontend_dependencies() {
  if [[ ! -x "$FRONTEND_DIR/node_modules/.bin/playwright" ]]; then
    (
      cd "$FRONTEND_DIR"
      "$NPM_BIN" ci
    )
    return
  fi

  if [[ "$FRONTEND_DIR/package.json" -nt "$FRONTEND_DIR/node_modules/.bin/playwright" ]]; then
    (
      cd "$FRONTEND_DIR"
      "$NPM_BIN" ci
    )
    return
  fi

  if [[ "$FRONTEND_DIR/package-lock.json" -nt "$FRONTEND_DIR/node_modules/.bin/playwright" ]]; then
    (
      cd "$FRONTEND_DIR"
      "$NPM_BIN" ci
    )
  fi
}

playwright_version() {
  local version
  local package_json="$FRONTEND_DIR/node_modules/playwright/package.json"

  if [[ ! -f "$package_json" ]]; then
    return 1
  fi

  version="$({ sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$package_json" | head -n 1; } || true)"
  [[ -n "$version" ]] || return 1
  printf '%s\n' "$version"
}

ensure_browser() {
  local current_version=""
  local recorded_version=""

  current_version="$(playwright_version || true)"
  if [[ -n "$current_version" && -f "$BROWSER_SENTINEL_PATH" ]]; then
    recorded_version="$(<"$BROWSER_SENTINEL_PATH")"
    if [[ "$recorded_version" == "$current_version" ]] && compgen -G "$BROWSER_CACHE_DIR/chromium-*" >/dev/null; then
      return
    fi
  fi

  (
    cd "$FRONTEND_DIR"
    "$NPM_BIN" run e2e:install
  )

  if [[ -n "$current_version" ]]; then
    mkdir -p "$BROWSER_CACHE_DIR"
    printf '%s\n' "$current_version" >"$BROWSER_SENTINEL_PATH"
  fi
}

reset_auth_profile() {
  compose exec -T web python manage.py auth_profile_reset
}

run_playwright() {
  (
    cd "$FRONTEND_DIR"
    if [[ ${#playwright_args[@]} -gt 0 ]]; then
      "$NPM_BIN" run e2e:auth-profile -- "${playwright_args[@]}"
    else
      "$NPM_BIN" run e2e:auth-profile
    fi
  )
}

command_name="run"
reset_requested="yes"
playwright_args=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    run|up|reset|install|down|help)
      command_name="$1"
      shift
      ;;
    --no-reset)
      reset_requested="no"
      shift
      ;;
    --headed|--ui)
      playwright_args+=("$1")
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ "$command_name" != "run" && ${#playwright_args[@]} -gt 0 ]]; then
  echo "Playwright flags are only supported for the default run command" >&2
  usage >&2
  exit 1
fi

case "$command_name" in
  run)
    ensure_stack
    ensure_frontend_dependencies
    ensure_browser
    if [[ "$reset_requested" == "yes" ]]; then
      reset_auth_profile
    fi
    run_playwright
    ;;
  up)
    ensure_stack
    ;;
  reset)
    ensure_stack
    reset_auth_profile
    ;;
  install)
    ensure_frontend_dependencies
    ensure_browser
    ;;
  down)
    compose down
    ;;
  help)
    usage
    ;;
esac