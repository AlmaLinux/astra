#!/usr/bin/env bash
set -euo pipefail

# Dump users and values for a single non-empty FreeIPA attribute.
# Output format: username,value

ATTR_NAME="${1:-${COUNTRY_ATTR:-st}}"

if ! command -v ipa >/dev/null 2>&1; then
  echo "ipa CLI not found in PATH." >&2
  exit 1
fi

if ! ipa ping >/dev/null 2>&1; then
  echo "Cannot reach/authenticate to FreeIPA. Run kinit and verify IPA config." >&2
  exit 1
fi

ipa user-find --all --raw --sizelimit=0 | awk -v attr="$ATTR_NAME" '
BEGIN {
  IGNORECASE = 1
  username = ""
  value_count = 0
}

function flush_entry(    i) {
  if (username == "") {
    return
  }

  for (i = 1; i <= value_count; i++) {
    if (values[i] != "") {
      print username "," values[i]
    }
  }

  username = ""
  value_count = 0
  delete values
}

{
  line = $0
  gsub(/\r$/, "", line)
  sub(/^[[:space:]]+/, "", line)

  if (line ~ /^uid:[[:space:]]*/) {
    flush_entry()
    sub(/^uid:[[:space:]]*/, "", line)
    username = line
    next
  }

  if (line ~ ("^" attr ":[[:space:]]*")) {
    sub(("^" attr ":[[:space:]]*"), "", line)
    if (line != "") {
      values[++value_count] = line
    }
    next
  }

  if (line ~ /^[-]+$/ || line == "") {
    flush_entry()
  }
}

END {
  flush_entry()
}
'
