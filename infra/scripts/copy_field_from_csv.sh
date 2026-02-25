#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  copy_field_from_csv.sh TARGET_ATTR INPUT_CSV [--dry-run|--apply] [--force]

Input CSV format:
  username,value

Examples:
  copy_field_from_csv.sh c ./country_dump.csv --dry-run
  copy_field_from_csv.sh c ./country_dump.csv --apply
  copy_field_from_csv.sh c ./country_dump.csv --apply --force
EOF
}

if [[ $# -lt 2 ]]; then
  usage >&2
  exit 1
fi

TARGET_ATTR="$1"
INPUT_CSV="$2"
MODE="--dry-run"
FORCE_OVERWRITE=false

for arg in "${@:3}"; do
  case "$arg" in
    --dry-run|--apply)
      MODE="$arg"
      ;;
    --force)
      FORCE_OVERWRITE=true
      ;;
    *)
      echo "Invalid argument: $arg" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ ! -f "$INPUT_CSV" ]]; then
  echo "Input CSV not found: $INPUT_CSV" >&2
  exit 1
fi

if ! command -v ipa >/dev/null 2>&1; then
  echo "ipa CLI not found in PATH." >&2
  exit 1
fi

if ! ipa ping >/dev/null 2>&1; then
  echo "Cannot reach/authenticate to FreeIPA. Run kinit and verify IPA config." >&2
  exit 1
fi

read_existing_values() {
  local username="$1"
  local attr_name="$2"
  local user_show_output=""

  if ! user_show_output="$(ipa user-show "$username" --all --raw 2>/dev/null)"; then
    return 2
  fi

  awk -v attr="$attr_name" '
BEGIN { IGNORECASE = 1 }
{
  line = $0
  sub(/^[[:space:]]+/, "", line)
  if (line ~ ("^" attr ":[[:space:]]*")) {
    sub(("^" attr ":[[:space:]]*"), "", line)
    if (line != "") {
      print line
    }
  }
}
' <<< "$user_show_output" | paste -sd'|' -
}

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

updated_count=0
skipped_count=0
line_number=0

while IFS=, read -r username value extra; do
  line_number=$((line_number + 1))

  if [[ -z "${username}${value}${extra}" ]]; then
    continue
  fi

  username="$(trim "$username")"
  value="$(trim "$value")"

  if [[ "$username" == \#* ]]; then
    continue
  fi

  if [[ -n "$extra" ]]; then
    echo "Skipping line $line_number: expected exactly 2 columns (username,value)." >&2
    skipped_count=$((skipped_count + 1))
    continue
  fi

  if [[ -z "$username" || -z "$value" ]]; then
    echo "Skipping line $line_number: empty username or value." >&2
    skipped_count=$((skipped_count + 1))
    continue
  fi

  existing_values=""
  if ! existing_values="$(read_existing_values "$username" "$TARGET_ATTR")"; then
    echo "Skipping line $line_number: user lookup failed for '$username'." >&2
    skipped_count=$((skipped_count + 1))
    continue
  fi

  if [[ -n "$existing_values" ]]; then
    echo "WARNING line $line_number: username '$username' already has ${TARGET_ATTR}='$existing_values'." >&2
    if [[ "$FORCE_OVERWRITE" != true ]]; then
      echo "Skipping line $line_number: destination field already has a value (use --force to overwrite)." >&2
      skipped_count=$((skipped_count + 1))
      continue
    fi
  fi

  if [[ "$MODE" == "--apply" ]]; then
    ipa user-mod "$username" --setattr="${TARGET_ATTR}=${value}" >/dev/null
    echo "updated,$username,$value"
  else
    echo "dry-run,$username,${TARGET_ATTR},${value}"
  fi

  updated_count=$((updated_count + 1))
done < "$INPUT_CSV"

echo "Processed $updated_count rows; skipped $skipped_count rows." >&2
