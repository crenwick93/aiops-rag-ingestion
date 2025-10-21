#!/usr/bin/env bash
set -euo pipefail

# Find the Confluence page ID for a parent page by name ("folder").
# Usage:
#   export CONF_CLOUD_ID="<cloud_id>"   # required (ex route)
#   export CONF_USER="<email>"
#   export CONF_API_TOKEN="<api_token>"
#   # Optional scoping:
#   # export SPACEKEY="~701214d81fc9f3b774a63ba642a08ad97bfa1"  # personal space or normal key
#   # export KNOWN_CHILD_ID=<a child page id under the parent>
#   ./get_folder_id.sh "Known Issues"
#
# Output:
#   Prints ONLY the discovered page id to stdout.

if [ $# -lt 1 ]; then
  echo "Usage: get_folder_id.sh <folder_name>" >&2
  exit 2
fi

TITLE="$1"

require() { if [ -z "${!1:-}" ]; then echo "Missing env: $1" >&2; exit 2; fi; }
require CONF_CLOUD_ID; require CONF_USER; require CONF_API_TOKEN

# API base for Confluence (ex route)
API_BASE="https://api.atlassian.com/ex/confluence/$CONF_CLOUD_ID/wiki/rest/api"

# If SPACEKEY is not set but SPACE_NAME is provided, resolve it to SPACEKEY
if [ -z "${SPACEKEY:-}" ] && [ -n "${SPACE_NAME:-}" ]; then
  SPACEKEY=$(curl -sS -u "$CONF_USER:$CONF_API_TOKEN" "$API_BASE/space?limit=250" \
    | jq -r --arg NAME "$SPACE_NAME" '.results[] | select(.name==$NAME) | .key' | head -n1)
fi

json_get_first_id() {
  jq -r '.results[0].id // empty'
}

search_once() {
  local cql="$1"
  curl -sS -u "$CONF_USER:$CONF_API_TOKEN" --get \
    --data-urlencode "cql=$cql" \
    --data-urlencode 'limit=5' \
    "$API_BASE/content/search"
}

find_by_title() {
  local id=""
  if [ -n "${SPACEKEY:-}" ]; then
    # Exact title in a specific space
    id=$(search_once "space=$SPACEKEY and type=page and title=\"$TITLE\"" | json_get_first_id)
    if [ -n "$id" ]; then echo "$id"; return; fi
    # Fuzzy title in a specific space
    id=$(search_once "space=$SPACEKEY and type=page and title ~ \"$TITLE\"" | json_get_first_id)
    if [ -n "$id" ]; then echo "$id"; return; fi
  fi
  # Site-wide exact title
  id=$(search_once "type=page and title=\"$TITLE\"" | json_get_first_id)
  if [ -n "$id" ]; then echo "$id"; return; fi
  # Site-wide fuzzy title
  id=$(search_once "type=page and title ~ \"$TITLE\"" | json_get_first_id)
  if [ -n "$id" ]; then echo "$id"; return; fi
  echo ""  # not found
}

find_from_child() {
  local child_id="$1"; [ -z "$child_id" ] && return 0
  curl -sS -u "$CONF_USER:$CONF_API_TOKEN" \
    "$API_BASE/content/$child_id?expand=ancestors" \
    | jq -r --arg TITLE "$TITLE" '.ancestors[] | select(.title==$TITLE) | .id' | head -n1
}

PARENT_ID=""
if [ -n "${KNOWN_CHILD_ID:-}" ]; then
  PARENT_ID=$(find_from_child "$KNOWN_CHILD_ID" || true)
fi
if [ -z "$PARENT_ID" ]; then
  PARENT_ID=$(find_by_title || true)
fi

if [ -z "$PARENT_ID" ]; then
  echo "Could not locate a page titled '$TITLE'. Consider setting SPACEKEY or KNOWN_CHILD_ID." >&2
  exit 1
fi

echo "$PARENT_ID"

