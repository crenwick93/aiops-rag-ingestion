#!/usr/bin/env bash
set -euo pipefail

: "${VECTOR_API_BASE_URL:?Set VECTOR_API_BASE_URL, e.g. http://vector-api:8001}"
VECTOR_DB_ID="${VECTOR_DB_ID:-confluence-test}"
EMBEDDING_MODEL_ID="${EMBEDDING_MODEL_ID:-all-MiniLM-L6-v2}"
VECTOR_DB_PROVIDER="${VECTOR_DB_PROVIDER:-sqlite-vec}"

request() {
  local method="$1" url="$2" body_file="${3:-}"
  local hdr body code ctype
  hdr="$(mktemp)"; body="$(mktemp)"
  if [ -n "$body_file" ]; then
    curl -sS -X "$method" "$url" -H 'content-type: application/json' -d @"$body_file" -D "$hdr" -o "$body" || true
  else
    curl -sS -X "$method" "$url" -D "$hdr" -o "$body" || true
  fi
  code="$(awk 'NR==1{print $2}' "$hdr")"
  ctype="$(awk 'BEGIN{IGNORECASE=1}/^Content-Type:/{print $2}' "$hdr" | tr -d '\r')"
  echo "HTTP $code | Content-Type: ${ctype:-unknown} | $url"
  if echo "${ctype}" | grep -qi json; then
    jq . "$body" 2>/dev/null || sed -n '1,200p' "$body"
  else
    sed -n '1,200p' "$body"
  fi
  rm -f "$hdr" "$body"
}

echo "Discovering vector endpoints…"
paths=$(curl -sS "$VECTOR_API_BASE_URL/openapi.json" | jq -r '.paths | keys[]')
if grep -q '^/v1/vector-io/insert$' <<<"$paths"; then
  VARIANT="dash"
  REGISTER_URL="$VECTOR_API_BASE_URL/v1/vector-dbs"
  INSERT_URL="$VECTOR_API_BASE_URL/v1/vector-io/insert"
elif grep -q '^/v1/vector_io/collections$' <<<"$paths"; then
  VARIANT="underscore"
  REGISTER_URL="$VECTOR_API_BASE_URL/v1/vector_io/collections"
  INSERT_URL="$VECTOR_API_BASE_URL/v1/vector_io/documents"
else
  echo "No known vector endpoints found."; exit 2
fi
echo "Variant: $VARIANT"

tmp_req="$(mktemp)"
trap 'rm -f "$tmp_req"' EXIT

echo "Registering vector DB…"
if [ "$VARIANT" = "dash" ]; then
  cat >"$tmp_req" <<JSON
{
  "vector_db_id": "$VECTOR_DB_ID",
  "provider_id": "${VECTOR_DB_PROVIDER}",
  "embedding_model": "$EMBEDDING_MODEL_ID",
  "config": { "kvstore": { "type": "sqlite" } }
}
JSON
  request POST "$REGISTER_URL" "$tmp_req"
else
  cat >"$tmp_req" <<JSON
{
  "collection_id": "$VECTOR_DB_ID",
  "embedding_model_id": "$EMBEDDING_MODEL_ID",
  "metadata": {"source":"curl-test"}
}
JSON
  request POST "$REGISTER_URL" "$tmp_req"
fi

echo "Inserting chunks…"
if [ "$VARIANT" = "dash" ]; then
  cat >"$tmp_req" <<JSON
{
  "vector_db_id": "$VECTOR_DB_ID",
  "embedding_model_id": "$EMBEDDING_MODEL_ID",
  "chunks": [
    { "chunk_id": "doc-1", "content": "hello vector db", "metadata": {"document_id":"doc-1","k":"v"} },
    { "chunk_id": "doc-2", "content": "second chunk",   "metadata": {"document_id":"doc-2"} }
  ]
}
JSON
  request POST "$INSERT_URL" "$tmp_req"
else
  cat >"$tmp_req" <<JSON
{
  "collection_id": "$VECTOR_DB_ID",
  "documents": [
    { "document_id": "doc-1", "text": "hello vector db", "metadata": {"k":"v"} },
    { "document_id": "doc-2", "text": "second chunk" }
  ]
}
JSON
  request POST "$INSERT_URL" "$tmp_req"
fi

echo "Querying …"
if [ "$VARIANT" = "dash" ]; then
  cat >"$tmp_req" <<JSON
{
  "vector_db_id": "$VECTOR_DB_ID",
  "query": "hello",
  "top_k": 3
}
JSON
  request POST "$VECTOR_API_BASE_URL/v1/vector-io/query" "$tmp_req"
else
  cat >"$tmp_req" <<JSON
{
  "collection_id": "$VECTOR_DB_ID",
  "query": "hello",
  "top_k": 3
}
JSON
  request POST "$VECTOR_API_BASE_URL/v1/vector_io/query" "$tmp_req"
fi

echo "Done."