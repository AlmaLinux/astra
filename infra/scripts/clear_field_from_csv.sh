#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  clear_field_from_csv.sh OLD_ATTR INPUT_CSV [--dry-run|--apply]

Input CSV format:
  username,value

Examples:
  clear_field_from_csv.sh st ./country_dump.csv --dry-run
  clear_field_from_csv.sh st ./country_dump.csv --apply
EOF
}

if [[ $# -lt 2 ]]; then
  usage >&2
  exit 1
fi

OLD_ATTR="$1"
INPUT_CSV="$2"
MODE="--dry-run"

for arg in "${@:3}"; do
  case "$arg" in
    --dry-run|--apply)
      MODE="$arg"
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

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

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
' <<< "$user_show_output"
}

cleared_count=0
skipped_count=0
line_number=0

while IFS=, read -r username dumped_value extra; do
  line_number=$((line_number + 1))

  if [[ -z "${username}${dumped_value}${extra}" ]]; then
    continue
  fi

  username="$(trim "$username")"
  dumped_value="$(trim "$dumped_value")"

  if [[ "$username" == \#* ]]; then
    continue
  fi

  if [[ -n "$extra" ]]; then
    echo "Skipping line $line_number: expected exactly 2 columns (username,value)." >&2
    skipped_count=$((skipped_count + 1))
    continue
  fi

  if [[ -z "$username" ]]; then
    echo "Skipping line $line_number: empty username." >&2
    skipped_count=$((skipped_count + 1))
    continue
  fi

  mapfile -t existing_values < <(read_existing_values "$username" "$OLD_ATTR") || {
    echo "Skipping line $line_number: user lookup failed for '$username'." >&2
    skipped_count=$((skipped_count + 1))
    continue
  }

  if [[ ${#existing_values[@]} -eq 0 ]]; then
    echo "Skipping line $line_number: username '$username' already has empty ${OLD_ATTR}." >&2
    skipped_count=$((skipped_count + 1))
    continue
  fi

  existing_joined="$(printf '%s|' "${existing_values[@]}")"
  existing_joined="${existing_joined%|}"

  if [[ -n "$dumped_value" ]]; then
    dumped_found=false
    for existing in "${existing_values[@]}"; do
      if [[ "$existing" == "$dumped_value" ]]; then
        dumped_found=true
        break
      fi
    done
    if [[ "$dumped_found" != true ]]; then
      echo "WARNING line $line_number: dump value '$dumped_value' is not present in current ${OLD_ATTR} for '$username' (current='${existing_joined}')." >&2
    fi
  fi

  if [[ "$MODE" == "--apply" ]]; then
    delattr_args=()
    for existing in "${existing_values[@]}"; do
      delattr_args+=("--delattr=${OLD_ATTR}=${existing}")
    done

    ipa user-mod "$username" "${delattr_args[@]}" >/dev/null
    echo "cleared,$username,${OLD_ATTR},${existing_joined}"
  else
    echo "dry-run-clear,$username,${OLD_ATTR},${existing_joined}"
  fi

  cleared_count=$((cleared_count + 1))
done < "$INPUT_CSV"

echo "Processed $cleared_count rows; skipped $skipped_count rows." >&2
