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
DETECTABLE_PROJECT_SERVICES=(db minio minio_init web mailhog vite)
AUTH_THEME="auth"
COMMITTEE_THEME="membership-committee"
INVITATIONS_THEME="membership-invitations"
ORGANIZATIONS_THEME="organizations"
GROUPS_THEME="groups"
ELECTIONS_THEME="elections"
MEMBERSHIP_SETTINGS_THEME="membership-settings"
MAIL_TOOLS_THEME="mail-tools"
SHELL_ROUTES_THEME="shell-routes"
REPORTS_ADMIN_THEME="reports-admin"
SELF_SERVICE_THEME="membership-self-service"
RAW_PLAYWRIGHT_SCRIPT="e2e:raw"
AUTH_RESET_STATE_ROOT="$REPO_ROOT/.e2e-reset-state"
AUTH_RESET_STATE_FILE=""
AUTH_RESET_STATE_OWNED="no"
ORGANIZATIONS_RESET_STATE_ROOT="$REPO_ROOT/.e2e-reset-state"
ORGANIZATIONS_RESET_STATE_FILE=""
ORGANIZATIONS_RESET_STATE_OWNED="no"
ELECTIONS_RESET_STATE_ROOT="$REPO_ROOT/.e2e-reset-state"
ELECTIONS_RESET_STATE_FILE=""
ELECTIONS_RESET_STATE_OWNED="no"
SELF_SERVICE_RESET_STATE_ROOT="$REPO_ROOT/.e2e-reset-state"
SELF_SERVICE_RESET_STATE_FILE=""
SELF_SERVICE_RESET_STATE_OWNED="no"
ORGANIZATIONS_LIST_DETAIL_SPEC="e2e/organizations/list-detail.spec.ts"
ORGANIZATIONS_CLAIM_SPEC="e2e/organizations/claim.spec.ts"
GROUPS_LIST_DETAIL_SPEC="e2e/groups/list-detail.spec.ts"
GROUPS_MANAGEMENT_SPEC="e2e/groups/management.spec.ts"
ELECTIONS_LIST_DETAIL_SPEC="e2e/elections/list-detail.spec.ts"
ELECTIONS_ROUTES_SHELL_SPEC="e2e/elections/routes-shell.spec.ts"
ELECTIONS_BALLOT_VERIFY_SPEC="e2e/elections/ballot-verify.spec.ts"
ELECTIONS_LIFECYCLE_SPEC="e2e/elections/lifecycle.spec.ts"
MEMBERSHIP_SETTINGS_SPEC="e2e/membership/settings-membership.spec.ts"
MAIL_TOOLS_SPEC="e2e/mail-tools.spec.ts"
SHELL_ROUTES_SPEC="e2e/shell-routes.spec.ts"
REPORTS_ADMIN_SPEC="e2e/reports-admin.spec.ts"
SELF_SERVICE_ENTRY_SPEC="e2e/membership/self-service-entry.spec.ts"
SELF_SERVICE_DETAIL_SPEC="e2e/membership/self-service-detail.spec.ts"
ALL_THEME_NAMES=(
  "$AUTH_THEME"
  "$COMMITTEE_THEME"
  "$INVITATIONS_THEME"
  "$ORGANIZATIONS_THEME"
  "$GROUPS_THEME"
  "$ELECTIONS_THEME"
  "$MEMBERSHIP_SETTINGS_THEME"
  "$MAIL_TOOLS_THEME"
  "$SHELL_ROUTES_THEME"
  "$REPORTS_ADMIN_THEME"
  "$SELF_SERVICE_THEME"
)

declare -A THEME_SCRIPT_NAMES=(
  ["$AUTH_THEME"]="e2e:auth"
  ["$COMMITTEE_THEME"]="e2e:membership-committee"
  ["$INVITATIONS_THEME"]="e2e:membership-invitations"
  ["$ORGANIZATIONS_THEME"]="e2e:organizations"
  ["$GROUPS_THEME"]="e2e:groups"
  ["$ELECTIONS_THEME"]="e2e:elections"
  ["$MEMBERSHIP_SETTINGS_THEME"]="e2e:membership-settings:playwright"
  ["$MAIL_TOOLS_THEME"]="e2e:mail-tools"
  ["$SHELL_ROUTES_THEME"]="e2e:shell-routes"
  ["$REPORTS_ADMIN_THEME"]="e2e:reports-admin"
  ["$SELF_SERVICE_THEME"]="e2e:membership-self-service"
)
declare -A THEME_SPEC_PATHS=(
  ["$AUTH_THEME"]="e2e/auth"
  ["$COMMITTEE_THEME"]="e2e/membership/committee-queue.spec.ts"
  ["$INVITATIONS_THEME"]="e2e/membership/account-invitations.spec.ts"
  ["$ORGANIZATIONS_THEME"]="$ORGANIZATIONS_LIST_DETAIL_SPEC $ORGANIZATIONS_CLAIM_SPEC"
  ["$GROUPS_THEME"]="$GROUPS_LIST_DETAIL_SPEC $GROUPS_MANAGEMENT_SPEC"
  ["$ELECTIONS_THEME"]="$ELECTIONS_LIST_DETAIL_SPEC $ELECTIONS_ROUTES_SHELL_SPEC $ELECTIONS_BALLOT_VERIFY_SPEC $ELECTIONS_LIFECYCLE_SPEC"
  ["$MEMBERSHIP_SETTINGS_THEME"]="$MEMBERSHIP_SETTINGS_SPEC"
  ["$MAIL_TOOLS_THEME"]="$MAIL_TOOLS_SPEC"
  ["$SHELL_ROUTES_THEME"]="$SHELL_ROUTES_SPEC"
  ["$REPORTS_ADMIN_THEME"]="$REPORTS_ADMIN_SPEC"
  ["$SELF_SERVICE_THEME"]="$SELF_SERVICE_ENTRY_SPEC $SELF_SERVICE_DETAIL_SPEC"
)
declare -A SCENARIO_THEME_NAMES=(
  ["auth-login-shell"]="$AUTH_THEME"
  ["auth-profile-regular"]="$AUTH_THEME"
  ["auth-profile-admin-nav"]="$AUTH_THEME"
  ["settings-shell-tabs"]="$AUTH_THEME"
  ["public-auth-shells"]="$AUTH_THEME"
  ["committee-queue-shell"]="$COMMITTEE_THEME"
  ["committee-pending-filter-renewals"]="$COMMITTEE_THEME"
  ["committee-pending-row-actions"]="$COMMITTEE_THEME"
  ["committee-pending-bulk-accept"]="$COMMITTEE_THEME"
  ["committee-on-hold-bulk-approve"]="$COMMITTEE_THEME"
  ["committee-row-actions"]="$COMMITTEE_THEME"
  ["committee-request-detail"]="$COMMITTEE_THEME"
  ["invitations-list-shell"]="$INVITATIONS_THEME"
  ["invitations-pending-row-actions"]="$INVITATIONS_THEME"
  ["invitations-pending-bulk-resend"]="$INVITATIONS_THEME"
  ["invitations-accepted-bulk-dismiss"]="$INVITATIONS_THEME"
  ["organizations-list-shell"]="$ORGANIZATIONS_THEME"
  ["organizations-sponsor-search-mirror-stability"]="$ORGANIZATIONS_THEME"
  ["organizations-detail-membership-state"]="$ORGANIZATIONS_THEME"
  ["organizations-claim-happy-path"]="$ORGANIZATIONS_THEME"
  ["organizations-claim-already-claimed"]="$ORGANIZATIONS_THEME"
  ["groups-list-shell"]="$GROUPS_THEME"
  ["groups-list-search-pagination"]="$GROUPS_THEME"
  ["groups-detail-nested-members"]="$GROUPS_THEME"
  ["groups-detail-chat-links"]="$GROUPS_THEME"
  ["groups-detail-leaders-pagination"]="$GROUPS_THEME"
  ["elections-list-viewer-shell"]="$ELECTIONS_THEME"
  ["elections-list-manager-draft-routing"]="$ELECTIONS_THEME"
  ["elections-detail-open-summary"]="$ELECTIONS_THEME"
  ["elections-detail-tallied-results"]="$ELECTIONS_THEME"
  ["elections-vote-ineligible-state"]="$ELECTIONS_THEME"
  ["elections-detail-operator-actions"]="$ELECTIONS_THEME"
  ["elections-turnout-report-shell"]="$ELECTIONS_THEME"
  ["elections-audit-log-finished-shell"]="$ELECTIONS_THEME"
  ["elections-ballot-verify-closed-public-state"]="$ELECTIONS_THEME"
  ["elections-ballot-verify-tallied-public-states"]="$ELECTIONS_THEME"
  ["elections-email-open-reminder"]="$ELECTIONS_THEME"
  ["elections-email-closed-send"]="$ELECTIONS_THEME"
  ["elections-email-tallied-send"]="$ELECTIONS_THEME"
  ["membership-settings-shell"]="$MEMBERSHIP_SETTINGS_THEME"
  ["membership-settings-history-and-exit-controls"]="$MEMBERSHIP_SETTINGS_THEME"
  ["mail-tools-send-mail-workflow"]="$MAIL_TOOLS_THEME"
  ["mail-tools-template-manager-and-images"]="$MAIL_TOOLS_THEME"
  ["shell-routes-users-search-and-static-links"]="$SHELL_ROUTES_THEME"
  ["shell-routes-notifications-and-sidebar"]="$SHELL_ROUTES_THEME"
  ["reports-admin-audit-sponsors-stats"]="$REPORTS_ADMIN_THEME"
  ["reports-admin-django-admin-and-imports"]="$REPORTS_ADMIN_THEME"
  ["membership-create-individual"]="$SELF_SERVICE_THEME"
  ["membership-duplicate-individual"]="$SELF_SERVICE_THEME"
  ["membership-renewal-prefill-mirror"]="$SELF_SERVICE_THEME"
  ["membership-profile-pending-links"]="$SELF_SERVICE_THEME"
  ["membership-resubmit-on-hold-mirror"]="$SELF_SERVICE_THEME"
  ["membership-rescind-pending-individual"]="$SELF_SERVICE_THEME"
)

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
Usage: scripts/auth-profile-e2e.sh [run] [--theme <theme>] [--scenario <scenario>] [--no-reset] [--no-rebuild] [--headed] [--ui] [spec-path ...]
       scripts/auth-profile-e2e.sh up [--no-rebuild]
       scripts/auth-profile-e2e.sh reset [--theme <theme>] [--no-rebuild]
       scripts/auth-profile-e2e.sh install
       scripts/auth-profile-e2e.sh down
       scripts/auth-profile-e2e.sh list-projects
       scripts/auth-profile-e2e.sh cleanup-stale
       scripts/auth-profile-e2e.sh help

Commands:
  run      Start or reuse the E2E stack, wait for readiness, run the ordered reset sequence
           for the selected E2E theme(s), and execute the matching Playwright suite.
           The wrapper refreshes only the web service with a rebuild and forced recreate
           before resets so reused stacks pick up code and dependency changes.
           When no theme, scenario, or spec path is supplied, runs all available wrapper E2E tests under Chromium.
  up       Start or reuse the E2E stack and wait for /readyz.
  reset    Start or reuse the E2E stack, wait for /readyz, and run the ordered reset sequence
           for the selected E2E theme(s). Default: auth only.
  install  Ensure frontend dependencies and the Chromium browser are installed.
  down     Stop the isolated astra-e2e stack.
  list-projects
           List detected astra-e2e compose project names from local containers.
  cleanup-stale
           Stop detected astra-e2e-* compose projects other than the current ASTRA_E2E_PROJECT.
  help     Show this help output.

Options:
  --theme <theme>       Theme: auth, membership-committee, membership-invitations, organizations, groups, elections, membership-settings, mail-tools, shell-routes, reports-admin, or membership-self-service. May be repeated.
  --scenario <scenario> Named scenario. Maps to exactly one theme.
  --no-reset  Skip resets for default all-tests or explicit auth-only run commands.
  --no-rebuild Skip the web rebuild/recreate step when reusing or starting the E2E stack.
  --headed    Forward Playwright's --headed flag for the default run command.
  --ui        Forward Playwright's --ui flag for the default run command.

Themes:
  auth
  membership-committee
  membership-invitations
  organizations
  groups
  elections
  membership-settings
  mail-tools
  shell-routes
  reports-admin
  membership-self-service

Theme combinations:
  Themes may be combined, except membership-settings which remains standalone.

Scenarios:
  auth-login-shell
  auth-profile-regular
  auth-profile-admin-nav
  settings-shell-tabs
  public-auth-shells
  committee-queue-shell
  committee-pending-filter-renewals
  committee-pending-row-actions
  committee-pending-bulk-accept
  committee-on-hold-bulk-approve
  invitations-list-shell
  invitations-pending-row-actions
  invitations-pending-bulk-resend
  invitations-accepted-bulk-dismiss
  organizations-list-shell
  organizations-sponsor-search-mirror-stability
  organizations-detail-membership-state
  organizations-claim-happy-path
  organizations-claim-already-claimed
  groups-list-shell
  groups-list-search-pagination
  groups-detail-nested-members
  groups-detail-chat-links
  groups-detail-leaders-pagination
  elections-list-viewer-shell
  elections-list-manager-draft-routing
  elections-detail-open-summary
  elections-detail-tallied-results
  elections-vote-ineligible-state
  elections-detail-operator-actions
  elections-turnout-report-shell
  elections-audit-log-finished-shell
  elections-ballot-verify-closed-public-state
  elections-ballot-verify-tallied-public-states
  membership-settings-shell
  membership-settings-history-and-exit-controls
  mail-tools-send-mail-workflow
  mail-tools-template-manager-and-images
  shell-routes-users-search-and-static-links
  shell-routes-notifications-and-sidebar
  reports-admin-audit-sponsors-stats
  reports-admin-django-admin-and-imports
  membership-create-individual
  membership-duplicate-individual
  membership-renewal-prefill-mirror
  membership-profile-pending-links
  membership-resubmit-on-hold-mirror
  membership-rescind-pending-individual

Reset-state handoff:
  auth_profile_reset is captured to a deterministic JSON file and exported to Playwright as ASTRA_E2E_AUTH_RESET_STATE_FILE.
  organizations_reset is captured to a deterministic JSON file and exported to Playwright as ASTRA_E2E_RESET_STATE_FILE.
  elections_reset is captured to a deterministic JSON file and exported to Playwright as ASTRA_E2E_ELECTIONS_RESET_STATE_FILE.
  membership_selfservice_reset is captured to a deterministic JSON file and exported to Playwright as ASTRA_E2E_SELF_SERVICE_RESET_STATE_FILE.
  groups_reset emits deterministic diagnostics, but no groups-specific reset-state file is part of the executable contract.
  Organizations, elections, and self-service reset-state files are cleaned up independently.

Environment overrides:
  ASTRA_E2E_COMPOSE_COMMAND  Override the compose entry command.
  ASTRA_E2E_PROJECT          Override the compose project name. Default: astra-e2e.
  ASTRA_E2E_ENV_FILE         Override the env file. Default: .env.e2e.
  ASTRA_E2E_READY_URL        Override the readiness URL. Default: http://127.0.0.1:18000/readyz.
  ASTRA_E2E_FRONTEND_DIR     Override the frontend directory. Default: ./frontend.
  ASTRA_E2E_AUTH_RESET_STATE_PATH Override the auth reset-state file path.
  ASTRA_E2E_RESET_STATE_PATH Override the organizations reset-state file path.
  ASTRA_E2E_ELECTIONS_RESET_STATE_PATH Override the elections reset-state file path.
  ASTRA_E2E_SELF_SERVICE_RESET_STATE_PATH Override the self-service reset-state file path.
EOF
}

compose() {
  "${COMPOSE_CMD[@]}" "$@"
}

compose_for_project() {
  local project_name="$1"
  shift

  local compose_cmd=(podman-compose)
  if [[ -n "${ASTRA_E2E_COMPOSE_COMMAND:-}" ]]; then
    read -r -a compose_cmd <<<"${ASTRA_E2E_COMPOSE_COMMAND}"
  fi
  compose_cmd+=(
    -p "$project_name"
    --env-file "$ENV_FILE"
    -f docker-compose.yml
    -f docker-compose.e2e.yml
  )

  "${compose_cmd[@]}" "$@"
}

discover_e2e_projects() {
  local container_name
  local service_name
  local project_name
  declare -A seen_projects=()

  while IFS= read -r container_name; do
    [[ -n "$container_name" ]] || continue
    for service_name in "${DETECTABLE_PROJECT_SERVICES[@]}"; do
      if [[ "$container_name" == *"_${service_name}_"* ]]; then
        project_name="${container_name%_${service_name}_*}"
        if [[ "$project_name" == astra-e2e* ]]; then
          seen_projects["$project_name"]=1
        fi
        break
      fi
    done
  done < <("$PODMAN_BIN" ps --all --format '{{.Names}}')

  if [[ ${#seen_projects[@]} -eq 0 ]]; then
    return 0
  fi

  printf '%s\n' "${!seen_projects[@]}" | LC_ALL=C sort
}

list_projects() {
  discover_e2e_projects
}

cleanup_stale_projects() {
  local project_name
  local removed_stale_project="no"
  local project_names=()

  mapfile -t project_names < <(discover_e2e_projects)
  for project_name in "${project_names[@]}"; do
    if [[ "$project_name" == "$COMPOSE_PROJECT" ]]; then
      continue
    fi

    echo "Removing stale E2E project: $project_name"
    compose_for_project "$project_name" down
    removed_stale_project="yes"
  done

  if [[ "$removed_stale_project" == "no" ]]; then
    echo "No stale E2E projects found."
  fi
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

refresh_web_service() {
  echo "Refreshing astra-e2e web service to rebuild and recreate code changes"
  compose up -d --build --force-recreate --no-deps web
}

restart_web_service_for_runtime_state() {
  echo "Restarting astra-e2e web service so fake FreeIPA reset state is visible to web workers"
  compose restart web
  wait_until_ready
}

ensure_stack() {
  if stack_running; then
    echo "Reusing astra-e2e stack"
  else
    echo "Starting astra-e2e stack"
    compose up -d "${SERVICES[@]}"
  fi
  if [[ "$rebuild_requested" == "yes" ]]; then
    refresh_web_service
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
  local reset_payload
  local reset_state_file

  cleanup_auth_reset_state
  reset_state_file="$(auth_reset_state_file_path)"
  AUTH_RESET_STATE_FILE="$reset_state_file"
  if [[ -z "${ASTRA_E2E_AUTH_RESET_STATE_PATH:-}" ]]; then
    AUTH_RESET_STATE_OWNED="yes"
  fi
  export ASTRA_E2E_AUTH_RESET_STATE_FILE="$AUTH_RESET_STATE_FILE"

  reset_payload="$(compose exec -T web python manage.py auth_profile_reset)"
  mkdir -p "$(dirname "$reset_state_file")"
  printf '%s\n' "$reset_payload" >"$reset_state_file"
}

reset_membership_selfservice() {
  local reset_payload
  local reset_state_file

  cleanup_self_service_reset_state
  reset_state_file="$(self_service_reset_state_file_path)"
  SELF_SERVICE_RESET_STATE_FILE="$reset_state_file"
  if [[ -z "${ASTRA_E2E_SELF_SERVICE_RESET_STATE_PATH:-}" ]]; then
    SELF_SERVICE_RESET_STATE_OWNED="yes"
  fi
  export ASTRA_E2E_SELF_SERVICE_RESET_STATE_FILE="$SELF_SERVICE_RESET_STATE_FILE"

  reset_payload="$(compose exec -T web python manage.py membership_selfservice_reset)"
  mkdir -p "$(dirname "$reset_state_file")"
  printf '%s\n' "$reset_payload" >"$reset_state_file"
}

reset_membership_invitations() {
  compose exec -T web python manage.py account_invitations_reset
}

reset_elections() {
  local reset_payload
  local reset_state_file

  cleanup_elections_reset_state
  reset_state_file="$(elections_reset_state_file_path)"
  ELECTIONS_RESET_STATE_FILE="$reset_state_file"
  if [[ -z "${ASTRA_E2E_ELECTIONS_RESET_STATE_PATH:-}" ]]; then
    ELECTIONS_RESET_STATE_OWNED="yes"
  fi
  export ASTRA_E2E_ELECTIONS_RESET_STATE_FILE="$ELECTIONS_RESET_STATE_FILE"

  reset_payload="$(compose exec -T web python manage.py elections_reset)"
  mkdir -p "$(dirname "$reset_state_file")"
  printf '%s\n' "$reset_payload" >"$reset_state_file"
}

cleanup_auth_reset_state() {
  if [[ "$AUTH_RESET_STATE_OWNED" == "yes" && -n "$AUTH_RESET_STATE_FILE" ]]; then
    rm -f "$AUTH_RESET_STATE_FILE"
    rmdir "$(dirname "$AUTH_RESET_STATE_FILE")" 2>/dev/null || true
  fi

  AUTH_RESET_STATE_FILE=""
  AUTH_RESET_STATE_OWNED="no"
  unset ASTRA_E2E_AUTH_RESET_STATE_FILE || true
}

cleanup_organizations_reset_state() {
  if [[ "$ORGANIZATIONS_RESET_STATE_OWNED" == "yes" && -n "$ORGANIZATIONS_RESET_STATE_FILE" ]]; then
    rm -f "$ORGANIZATIONS_RESET_STATE_FILE"
    rmdir "$(dirname "$ORGANIZATIONS_RESET_STATE_FILE")" 2>/dev/null || true
  fi

  ORGANIZATIONS_RESET_STATE_FILE=""
  ORGANIZATIONS_RESET_STATE_OWNED="no"
  unset ASTRA_E2E_RESET_STATE_FILE || true
}

cleanup_elections_reset_state() {
  if [[ "$ELECTIONS_RESET_STATE_OWNED" == "yes" && -n "$ELECTIONS_RESET_STATE_FILE" ]]; then
    rm -f "$ELECTIONS_RESET_STATE_FILE"
    rmdir "$(dirname "$ELECTIONS_RESET_STATE_FILE")" 2>/dev/null || true
  fi

  ELECTIONS_RESET_STATE_FILE=""
  ELECTIONS_RESET_STATE_OWNED="no"
  unset ASTRA_E2E_ELECTIONS_RESET_STATE_FILE || true
}

cleanup_self_service_reset_state() {
  if [[ "$SELF_SERVICE_RESET_STATE_OWNED" == "yes" && -n "$SELF_SERVICE_RESET_STATE_FILE" ]]; then
    rm -f "$SELF_SERVICE_RESET_STATE_FILE"
    rmdir "$(dirname "$SELF_SERVICE_RESET_STATE_FILE")" 2>/dev/null || true
  fi

  SELF_SERVICE_RESET_STATE_FILE=""
  SELF_SERVICE_RESET_STATE_OWNED="no"
  unset ASTRA_E2E_SELF_SERVICE_RESET_STATE_FILE || true
}

reset_organizations() {
  local reset_payload
  local reset_state_file

  cleanup_organizations_reset_state
  reset_state_file="$(organizations_reset_state_file_path)"
  ORGANIZATIONS_RESET_STATE_FILE="$reset_state_file"
  if [[ -z "${ASTRA_E2E_RESET_STATE_PATH:-}" ]]; then
    ORGANIZATIONS_RESET_STATE_OWNED="yes"
  fi
  export ASTRA_E2E_RESET_STATE_FILE="$ORGANIZATIONS_RESET_STATE_FILE"

  reset_payload="$(compose exec -T web python manage.py organizations_reset)"
  mkdir -p "$(dirname "$reset_state_file")"
  printf '%s\n' "$reset_payload" >"$reset_state_file"
}

reset_groups() {
  local reset_payload

  reset_payload="$(compose exec -T web python manage.py groups_reset)"
  printf '%s\n' "$reset_payload"
}

reset_membership_committee() {
  compose exec -T web python manage.py membership_committee_reset
}

normalize_spec_path() {
  local spec_path="$1"
  spec_path="${spec_path#./}"
  spec_path="${spec_path#frontend/}"
  printf '%s\n' "$spec_path"
}

auth_reset_state_file_path() {
  if [[ -n "${ASTRA_E2E_AUTH_RESET_STATE_PATH:-}" ]]; then
    printf '%s\n' "$ASTRA_E2E_AUTH_RESET_STATE_PATH"
    return
  fi

  mkdir -p "$AUTH_RESET_STATE_ROOT"
  mktemp "$AUTH_RESET_STATE_ROOT/auth-reset-state.XXXXXX.json"
}

organizations_reset_state_file_path() {
  if [[ -n "${ASTRA_E2E_RESET_STATE_PATH:-}" ]]; then
    printf '%s\n' "$ASTRA_E2E_RESET_STATE_PATH"
    return
  fi

  mkdir -p "$ORGANIZATIONS_RESET_STATE_ROOT"
  mktemp "$ORGANIZATIONS_RESET_STATE_ROOT/organizations-reset-state.XXXXXX.json"
}

elections_reset_state_file_path() {
  if [[ -n "${ASTRA_E2E_ELECTIONS_RESET_STATE_PATH:-}" ]]; then
    printf '%s\n' "$ASTRA_E2E_ELECTIONS_RESET_STATE_PATH"
    return
  fi

  mkdir -p "$ELECTIONS_RESET_STATE_ROOT"
  mktemp "$ELECTIONS_RESET_STATE_ROOT/elections-reset-state.XXXXXX.json"
}

self_service_reset_state_file_path() {
  if [[ -n "${ASTRA_E2E_SELF_SERVICE_RESET_STATE_PATH:-}" ]]; then
    printf '%s\n' "$ASTRA_E2E_SELF_SERVICE_RESET_STATE_PATH"
    return
  fi

  mkdir -p "$SELF_SERVICE_RESET_STATE_ROOT"
  mktemp "$SELF_SERVICE_RESET_STATE_ROOT/membership-selfservice-reset-state.XXXXXX.json"
}

infer_theme_from_spec_path() {
  local spec_path
  spec_path="$(normalize_spec_path "$1")"

  case "$spec_path" in
    e2e/auth/*)
      printf '%s\n' "$AUTH_THEME"
      ;;
    e2e/membership/committee-queue.spec.ts)
      printf '%s\n' "$COMMITTEE_THEME"
      ;;
    e2e/membership/account-invitations.spec.ts)
      printf '%s\n' "$INVITATIONS_THEME"
      ;;
    "$ORGANIZATIONS_LIST_DETAIL_SPEC"|"$ORGANIZATIONS_CLAIM_SPEC")
      printf '%s\n' "$ORGANIZATIONS_THEME"
      ;;
    "$GROUPS_LIST_DETAIL_SPEC"|"$GROUPS_MANAGEMENT_SPEC")
      printf '%s\n' "$GROUPS_THEME"
      ;;
    "$ELECTIONS_LIST_DETAIL_SPEC"|"$ELECTIONS_ROUTES_SHELL_SPEC"|"$ELECTIONS_BALLOT_VERIFY_SPEC"|"$ELECTIONS_LIFECYCLE_SPEC")
      printf '%s\n' "$ELECTIONS_THEME"
      ;;
    "$MEMBERSHIP_SETTINGS_SPEC")
      printf '%s\n' "$MEMBERSHIP_SETTINGS_THEME"
      ;;
    "$SELF_SERVICE_ENTRY_SPEC"|"$SELF_SERVICE_DETAIL_SPEC")
      printf '%s\n' "$SELF_SERVICE_THEME"
      ;;
    *)
      return 1
      ;;
  esac
}

append_unique() {
  local value="$1"
  shift
  local existing
  for existing in "$@"; do
    if [[ "$existing" == "$value" ]]; then
      return 0
    fi
  done
  resolved_themes+=("$value")
}

ensure_membership_settings_standalone() {
  local theme_name
  local saw_membership_settings="no"

  for theme_name in "$@"; do
    if [[ "$theme_name" == "$MEMBERSHIP_SETTINGS_THEME" ]]; then
      saw_membership_settings="yes"
      break
    fi
  done

  if [[ "$saw_membership_settings" == "yes" && $# -gt 1 ]]; then
    echo "membership-settings cannot be combined with other themes" >&2
    exit 1
  fi
}

resolve_themes() {
  local requested_theme
  local scenario_name
  local scenario_theme
  local spec_path
  local inferred_theme
  local default_all_themes="no"

  resolved_themes=()

  if [[ ${#requested_scenarios[@]} -gt 0 && ( ${#requested_themes[@]} -gt 0 || ${#raw_spec_paths[@]} -gt 0 ) ]]; then
    echo "Scenarios cannot be combined with explicit themes or raw spec paths" >&2
    exit 1
  fi

  if [[ ${#requested_scenarios[@]} -gt 1 ]]; then
    echo "Only one --scenario may be supplied per run" >&2
    exit 1
  fi

  for requested_theme in "${requested_themes[@]}"; do
    if [[ -z "${THEME_SCRIPT_NAMES[$requested_theme]:-}" ]]; then
      echo "Unknown theme: $requested_theme" >&2
      exit 1
    fi
    append_unique "$requested_theme" "${resolved_themes[@]}"
  done

  for scenario_name in "${requested_scenarios[@]}"; do
    scenario_theme="${SCENARIO_THEME_NAMES[$scenario_name]:-}"
    if [[ -z "$scenario_theme" ]]; then
      echo "Unknown scenario: $scenario_name" >&2
      exit 1
    fi
    append_unique "$scenario_theme" "${resolved_themes[@]}"
  done

  if [[ ${#raw_spec_paths[@]} -gt 0 ]]; then
    local raw_themes=()
    for spec_path in "${raw_spec_paths[@]}"; do
      inferred_theme="$(infer_theme_from_spec_path "$spec_path")" || {
        echo "Unsupported raw spec path: $spec_path" >&2
        exit 1
      }
      raw_themes+=("$inferred_theme")
    done

    if [[ ${#requested_themes[@]} -eq 0 ]]; then
      for inferred_theme in "${raw_themes[@]}"; do
        append_unique "$inferred_theme" "${resolved_themes[@]}"
      done
    else
      for inferred_theme in "${raw_themes[@]}"; do
        local matched_requested_theme="no"
        for requested_theme in "${resolved_themes[@]}"; do
          if [[ "$requested_theme" == "$inferred_theme" ]]; then
            matched_requested_theme="yes"
            break
          fi
        done
        if [[ "$matched_requested_theme" != "yes" ]]; then
          echo "Raw spec path theme $inferred_theme is incompatible with the requested theme set" >&2
          exit 1
        fi
      done
    fi
  fi

  if [[ ${#resolved_themes[@]} -eq 0 ]]; then
    if [[ "$command_name" == "run" ]]; then
      resolved_themes=("${ALL_THEME_NAMES[@]}")
      default_all_themes="yes"
    else
      resolved_themes=("$AUTH_THEME")
    fi
  fi

  if [[ "$default_all_themes" != "yes" ]]; then
    ensure_membership_settings_standalone "${resolved_themes[@]}"
  fi

}

run_theme_resets() {
  local theme_name
  local include_auth_reset="no"
  local include_membership_committee_reset="no"
  local include_membership_invitations_reset="no"
  local include_organizations_reset="no"
  local include_groups_reset="no"
  local include_elections_reset="no"
  local include_membership_reset="no"
  local restart_web_after_resets="no"

  for theme_name in "$@"; do
    case "$theme_name" in
      "$AUTH_THEME")
        include_auth_reset="yes"
        ;;
      "$COMMITTEE_THEME")
        include_auth_reset="yes"
        include_membership_committee_reset="yes"
        ;;
      "$INVITATIONS_THEME")
        include_auth_reset="yes"
        include_membership_invitations_reset="yes"
        ;;
      "$ORGANIZATIONS_THEME")
        include_auth_reset="yes"
        include_organizations_reset="yes"
        ;;
      "$GROUPS_THEME")
        include_auth_reset="yes"
        include_groups_reset="yes"
        ;;
      "$ELECTIONS_THEME")
        include_auth_reset="yes"
        include_elections_reset="yes"
        ;;
      "$MEMBERSHIP_SETTINGS_THEME")
        include_auth_reset="yes"
        include_membership_reset="yes"
        ;;
      "$MAIL_TOOLS_THEME")
        include_auth_reset="yes"
        ;;
      "$SHELL_ROUTES_THEME")
        include_auth_reset="yes"
        include_membership_committee_reset="yes"
        include_membership_invitations_reset="yes"
        include_organizations_reset="yes"
        ;;
      "$REPORTS_ADMIN_THEME")
        include_auth_reset="yes"
        include_membership_committee_reset="yes"
        include_organizations_reset="yes"
        ;;
      "$SELF_SERVICE_THEME")
        include_auth_reset="yes"
        include_membership_reset="yes"
        ;;
    esac
  done

  if [[ "$include_auth_reset" == "yes" ]]; then
    reset_auth_profile
    restart_web_after_resets="yes"
  fi
  if [[ "$include_membership_committee_reset" == "yes" ]]; then
    reset_membership_committee
    restart_web_after_resets="yes"
  fi
  if [[ "$include_membership_invitations_reset" == "yes" ]]; then
    reset_membership_invitations
    restart_web_after_resets="yes"
  fi
  if [[ "$include_organizations_reset" == "yes" ]]; then
    reset_organizations
    restart_web_after_resets="yes"
  else
    cleanup_organizations_reset_state
  fi
  if [[ "$include_groups_reset" == "yes" ]]; then
    reset_groups
    restart_web_after_resets="yes"
  fi
  if [[ "$include_elections_reset" == "yes" ]]; then
    reset_elections
    restart_web_after_resets="yes"
  else
    cleanup_elections_reset_state
  fi
  if [[ "$include_membership_reset" == "yes" ]]; then
    reset_membership_selfservice
    restart_web_after_resets="yes"
  else
    cleanup_self_service_reset_state
  fi

  if [[ "$restart_web_after_resets" == "yes" ]]; then
    restart_web_service_for_runtime_state
  fi
}

run_multi_theme_playwright() {
  local theme_name
  local spec_paths=()
  local theme_spec_paths=()

  for theme_name in "${resolved_themes[@]}"; do
    read -r -a theme_spec_paths <<<"${THEME_SPEC_PATHS[$theme_name]}"
    spec_paths+=("${theme_spec_paths[@]}")
  done

  run_playwright_command "$RAW_PLAYWRIGHT_SCRIPT" --project=chromium "${spec_paths[@]}" "${playwright_args[@]}"
}

run_playwright_command() {
  local npm_script_name="$1"
  shift

  (
    cd "$FRONTEND_DIR"
    if [[ $# -gt 0 ]]; then
      "$NPM_BIN" run "$npm_script_name" -- "$@"
      return
    fi
    "$NPM_BIN" run "$npm_script_name"
  )
}

playwright_arg_present() {
  local expected_arg="$1"
  local playwright_arg

  for playwright_arg in "${playwright_args[@]}"; do
    if [[ "$playwright_arg" == "$expected_arg" ]]; then
      return 0
    fi
  done

  return 1
}

run_selected_playwright() {
  local scenario_name
  local spec_path
  local grep_args=()
  local normalized_spec_paths=()

  for scenario_name in "${requested_scenarios[@]}"; do
    grep_args+=("--grep" "$scenario_name")
  done

  if [[ ${#raw_spec_paths[@]} -gt 0 ]]; then
    for spec_path in "${raw_spec_paths[@]}"; do
      normalized_spec_paths+=("$(normalize_spec_path "$spec_path")")
    done
    run_playwright_command "$RAW_PLAYWRIGHT_SCRIPT" --project=chromium "${normalized_spec_paths[@]}" "${playwright_args[@]}" "${grep_args[@]}"
    return
  fi

  if [[ ${#resolved_themes[@]} -gt 1 ]]; then
    run_multi_theme_playwright
    return
  fi

  run_playwright_command "${THEME_SCRIPT_NAMES[${resolved_themes[0]}]}" "${playwright_args[@]}" "${grep_args[@]}"
}

command_name="run"
reset_requested="yes"
rebuild_requested="yes"
playwright_args=()
requested_themes=()
requested_scenarios=()
raw_spec_paths=()
resolved_themes=()

trap 'cleanup_auth_reset_state; cleanup_organizations_reset_state; cleanup_elections_reset_state; cleanup_self_service_reset_state' EXIT

while [[ $# -gt 0 ]]; do
  case "$1" in
    run|up|reset|install|down|list-projects|cleanup-stale|help)
      command_name="$1"
      shift
      ;;
    --no-reset)
      reset_requested="no"
      shift
      ;;
    --no-rebuild)
      rebuild_requested="no"
      shift
      ;;
    --theme)
      if [[ $# -lt 2 ]]; then
        echo "--theme requires a value" >&2
        usage >&2
        exit 1
      fi
      requested_themes+=("$2")
      shift 2
      ;;
    --scenario)
      if [[ $# -lt 2 ]]; then
        echo "--scenario requires a value" >&2
        usage >&2
        exit 1
      fi
      requested_scenarios+=("$2")
      shift 2
      ;;
    --headed|--ui)
      playwright_args+=("$1")
      shift
      ;;
    *.spec.ts|e2e/*|frontend/e2e/*)
      raw_spec_paths+=("$1")
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

if [[ "$command_name" != "run" && ( ${#playwright_args[@]} -gt 0 || ${#requested_scenarios[@]} -gt 0 || ${#raw_spec_paths[@]} -gt 0 ) ]]; then
  echo "Playwright flags are only supported for the default run command" >&2
  usage >&2
  exit 1
fi

if [[ "$rebuild_requested" == "no" && "$command_name" != "run" && "$command_name" != "up" && "$command_name" != "reset" ]]; then
  echo "--no-rebuild is only supported for run, up, and reset commands" >&2
  usage >&2
  exit 1
fi

resolve_themes

if [[ "$command_name" == "run" && "$reset_requested" == "no" ]]; then
  if [[ ${#requested_themes[@]} -eq 0 && ${#requested_scenarios[@]} -eq 0 && ${#raw_spec_paths[@]} -eq 0 ]]; then
    :
  elif [[ ${#resolved_themes[@]} -ne 1 || "${resolved_themes[0]}" != "$AUTH_THEME" ]]; then
    echo "--no-reset is only supported for auth-only runs" >&2
    exit 1
  fi
fi

case "$command_name" in
  run)
    ensure_stack
    ensure_frontend_dependencies
    ensure_browser
    if [[ "$reset_requested" == "yes" ]]; then
      run_theme_resets "${resolved_themes[@]}"
    fi
    run_selected_playwright
    ;;
  up)
    ensure_stack
    ;;
  reset)
    ensure_stack
    run_theme_resets "${resolved_themes[@]}"
    ;;
  install)
    ensure_frontend_dependencies
    ensure_browser
    ;;
  down)
    compose down
    ;;
  list-projects)
    list_projects
    ;;
  cleanup-stale)
    cleanup_stale_projects
    ;;
  help)
    usage
    ;;
esac