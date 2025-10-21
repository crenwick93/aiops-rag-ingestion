import hashlib
import os
import re
import sys
from typing import Dict, Iterable, List, Tuple

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as html2text


VECTOR_API_BASE_URL = os.getenv("VECTOR_API_BASE_URL", os.getenv("LLAMA_BASE_URL", "")).rstrip("/")
# Site base URL is not required when using Atlassian ex route with cloud id
CONF_BASE_URL = os.getenv("CONF_BASE_URL", "").rstrip("/")
CONF_USER = os.getenv("CONF_USER", "")
CONF_API_TOKEN = os.getenv("CONF_API_TOKEN", "")
# OAuth 2.0 (3LO) optional inputs: when set, we use Bearer token + cloudId via api.atlassian.com
CONF_ACCESS_TOKEN = os.getenv("CONF_ACCESS_TOKEN", "")
CONF_CLOUD_ID = os.getenv("CONF_CLOUD_ID", "").strip()
COLLECTION_ID = os.getenv("COLLECTION_ID", "confluence")
EMBEDDING_MODEL_ID = os.getenv("EMBEDDING_MODEL_ID", "all-MiniLM-L6-v2")
SINCE_HOURS = os.getenv("SINCE_HOURS", "24")
CHUNK_TOKENS = int(os.getenv("CHUNK_TOKENS", "1000"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "180"))
MAX_CHUNKS_PER_INSERT = int(os.getenv("MAX_CHUNKS_PER_INSERT", "128"))
VERBOSE = os.getenv("VERBOSE", "1").lower() in ("1", "true", "yes")

# Vector API variant discovery (OpenAPI):
# - Newer builds expose: /v1/vector-io/insert, /v1/vector-io/query, /v1/vector-dbs
# - Legacy builds expose: /v1/vector_io/collections, /v1/vector_io/documents
VECTOR_VARIANT = "unknown"  # one of: dash, underscore, unknown
VECTOR_REGISTER_URL = ""    # POST for creating/registering a db/collection
VECTOR_INSERT_URL = ""      # POST for inserting documents
VECTOR_DB_PROVIDER = os.getenv("VECTOR_DB_PROVIDER", "sqlite-vec")

# Optional filters
FILTER_SPACE_KEYS = [s.strip() for s in os.getenv("FILTER_SPACE_KEYS", "").split(",") if s.strip()]
FILTER_LABELS = [s.strip() for s in os.getenv("FILTER_LABELS", "").split(",") if s.strip()]
FILTER_FOLDER_ID = os.getenv("FILTER_FOLDER_ID", "").strip()
FILTER_PAGE_IDS = [s.strip() for s in os.getenv("FILTER_PAGE_IDS", "").split(",") if s.strip()]


def ensure_env() -> None:
    missing: List[str] = []
    # Single supported mode: Basic auth via Atlassian ex route with cloud id
    if not CONF_CLOUD_ID:
        missing.append("CONF_CLOUD_ID")
    if not CONF_USER:
        missing.append("CONF_USER")
    if not CONF_API_TOKEN:
        missing.append("CONF_API_TOKEN")
    if missing:
        raise SystemExit(f"Missing environment variables: {', '.join(missing)}")


def ensure_collection() -> None:
    try:
        if VECTOR_VARIANT == "dash":
            # POST /v1/vector-dbs
            payload = {
                "vector_db_id": COLLECTION_ID,
                "provider_id": VECTOR_DB_PROVIDER,
                "embedding_model": EMBEDDING_MODEL_ID,
                "config": {"kvstore": {"type": "sqlite"}},
            }
            r = requests.post(VECTOR_REGISTER_URL, json=payload, timeout=30)
            if r.status_code not in (200, 201, 409):
                print("register vector_db failed:", r.status_code, r.text, file=sys.stderr)
            elif VERBOSE:
                print(f"Registered vector DB (dash): {COLLECTION_ID} -> {r.status_code}")
        elif VECTOR_VARIANT == "underscore":
            # POST /v1/vector_io/collections
            payload = {
                "collection_id": COLLECTION_ID,
                "embedding_model_id": EMBEDDING_MODEL_ID,
                "metadata": {"source": "confluence"},
            }
            r = requests.post(VECTOR_REGISTER_URL, json=payload, timeout=30)
            if r.status_code not in (200, 201, 409):
                print("create collection failed:", r.status_code, r.text, file=sys.stderr)
            elif VERBOSE:
                print(f"Registered collection (underscore): {COLLECTION_ID} -> {r.status_code}")
        else:
            print("Vector API not discovered; cannot create collection.", file=sys.stderr)
            raise SystemExit(2)
    except Exception as e:
        print("Create collection error:", e, file=sys.stderr)


def discover_vector_api() -> bool:
    """Discover which vector API variant is available and set endpoint URLs."""
    global VECTOR_VARIANT, VECTOR_REGISTER_URL, VECTOR_INSERT_URL
    try:
        if not VECTOR_API_BASE_URL:
            return False
        r = requests.get(f"{VECTOR_API_BASE_URL}/openapi.json", timeout=10)
        if r.status_code != 200:
            return False
        data = r.json()
        paths = data.get("paths", {}) if isinstance(data, dict) else {}
        if not isinstance(paths, dict):
            return False
        keys = list(paths.keys())
        # Prefer new dash variant
        if "/v1/vector-io/insert" in keys and "/v1/vector-dbs" in keys:
            VECTOR_VARIANT = "dash"
            VECTOR_REGISTER_URL = f"{VECTOR_API_BASE_URL}/v1/vector-dbs"
            VECTOR_INSERT_URL = f"{VECTOR_API_BASE_URL}/v1/vector-io/insert"
            if VERBOSE:
                print("Vector API: dash variant detected")
            return True
        # Legacy underscore variant
        if "/v1/vector_io/collections" in keys and "/v1/vector_io/documents" in keys:
            VECTOR_VARIANT = "underscore"
            VECTOR_REGISTER_URL = f"{VECTOR_API_BASE_URL}/v1/vector_io/collections"
            VECTOR_INSERT_URL = f"{VECTOR_API_BASE_URL}/v1/vector_io/documents"
            if VERBOSE:
                print("Vector API: underscore variant detected")
            return True
        return False
    except Exception:
        return False


def vector_insert_documents(payload: dict, timeout: int = 60) -> bool:
    if not VECTOR_INSERT_URL:
        return False
    try:
        r = requests.post(VECTOR_INSERT_URL, json=payload, timeout=timeout)
        if r.status_code not in (200, 201):
            print(f"insert failed: {r.status_code} {r.text}", file=sys.stderr)
        elif VERBOSE:
            print(f"insert ok: {r.status_code}")
        return True
    except requests.RequestException as e:
        print(f"insert error: {e}", file=sys.stderr)
        return True

def _vector_post(path: str, payload: dict, timeout: int = 60) -> bool:
    """
    POST to Llama Stack vector_io API.
    Returns False if the route is not found (HTTP 404) so caller can abort early.
    Returns True otherwise (even if non-2xx; caller already logs details).
    """
    if not VECTOR_API_BASE_URL:
        return False
    url = f"{VECTOR_API_BASE_URL}/v1/vector_io/{path}"
    try:
        r = requests.post(url, json=payload, timeout=timeout)
        if r.status_code == 404:
            return False
        if r.status_code not in (200, 201, 409):
            print(f"{path} failed: {r.status_code} {r.text}", file=sys.stderr)
        return True
    except requests.RequestException as e:
        print(f"{path} error: {e}", file=sys.stderr)
        return True


def _build_cql(hours: str) -> str:
    terms: List[str] = ["type=page"]
    if hours:
        terms.append(f'lastmodified > now("-{hours}h")')
    if FILTER_SPACE_KEYS:
        space_term = "(" + " OR ".join([f'space="{k}"' for k in FILTER_SPACE_KEYS]) + ")"
        terms.append(space_term)
    if FILTER_LABELS:
        label_term = "(" + " OR ".join([f'label="{l}"' for l in FILTER_LABELS]) + ")"
        terms.append(label_term)
    if FILTER_FOLDER_ID:
        terms.append(f"ancestor={FILTER_FOLDER_ID}")
    return " and ".join(terms)


def fetch_pages_since(hours: str) -> Iterable[dict]:
    s = requests.Session()
    s.auth = (CONF_USER, CONF_API_TOKEN)
    s.headers.update({"Accept": "application/json"})
    api_base = f"https://api.atlassian.com/ex/confluence/{CONF_CLOUD_ID}/wiki/rest/api"
    url = f"{api_base}/content/search"
    limit = 50
    params = {
        "cql": _build_cql(hours),
        "limit": limit,
        "expand": "body.export_view,version,metadata.labels,space,history.lastUpdated",
    }
    start = 0
    seen_ids: set[str] = set()
    while True:
        p = dict(params)
        p["start"] = start
        resp = s.get(url, params=p, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if not results:
            if VERBOSE:
                print("Pagination: no results, stopping")
            break
        new_count = 0
        for item in results:
            pid = item.get("id")
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            new_count += 1
            yield item
        # If server ignored 'start' or all were duplicates, stop to avoid loops
        if new_count == 0:
            if VERBOSE:
                print("Pagination: no new items (duplicates), stopping")
            break
        start += len(results)
        # If fewer than limit returned, we've reached the end
        if len(results) < limit:
            if VERBOSE:
                print(f"Pagination: received {len(results)} < limit {limit}, stopping")
            break


def fetch_pages_by_ids(ids: List[str]) -> Iterable[dict]:
    s = requests.Session()
    s.auth = (CONF_USER, CONF_API_TOKEN)
    s.headers.update({"Accept": "application/json"})
    api_base = f"https://api.atlassian.com/ex/confluence/{CONF_CLOUD_ID}/wiki/rest/api"
    for pid in ids:
        url = f"{api_base}/content/{pid}"
        params = {"expand": "body.export_view,version,metadata.labels,space,history.lastUpdated"}
        resp = s.get(url, params=params, timeout=60)
        if resp.status_code == 200:
            yield resp.json()
        else:
            print(f"Warn: failed to fetch page {pid}: {resp.status_code}", file=sys.stderr)


def normalize_markdown(html: str) -> str:
    # Convert HTML to Markdown while preserving structure (headings, links, lists)
    # Clean up excessive whitespace
    md = html2text(html or "", strip=['script', 'style'])
    md = re.sub(r"\s+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def rough_token_count(text: str) -> int:
    # Heuristic: ~4 chars per token for English
    return max(1, len(text) // 4)


def chunk_text(text: str, chunk_tokens: int, overlap_tokens: int) -> List[str]:
    # Token-approx using characters; keeps things dependency-free.
    if not text:
        return []
    approx_token = 4
    chunk_size_chars = chunk_tokens * approx_token
    overlap_chars = overlap_tokens * approx_token

    chunks: List[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + chunk_size_chars)
        chunk = text[start:end]
        chunks.append(chunk.strip())
        if end >= n:
            break
        start = max(0, end - overlap_chars)
    return [c for c in chunks if c]


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def upsert_chunks(page: dict) -> int:
    pid = page.get("id")
    title = page.get("title", "")
    body_html = (((page.get("body") or {}).get("export_view") or {}).get("value")) or ""
    space_key = ((page.get("space") or {}).get("key")) or ""
    labels = [l.get("name") for l in ((page.get("metadata") or {}).get("labels") or {}).get("results", []) if isinstance(l, dict)]
    version = ((page.get("version") or {}).get("number")) or 1
    last_updated = (((page.get("history") or {}).get("lastUpdated") or {}).get("when")) or ""

    md = normalize_markdown(body_html)
    chunks = chunk_text(md, CHUNK_TOKENS, CHUNK_OVERLAP)
    if VERBOSE:
        print(f"Page {pid} '{title}': {len(chunks)} chunks")

    docs = []
    for idx, chunk in enumerate(chunks):
        content_hash = sha256(chunk)
        doc_id = f"conf-{pid}-v{version}-{idx}-{content_hash[:8]}"
        docs.append({
            "document_id": doc_id,
            "text": chunk,
            "metadata": {
                "source": "confluence",
                "page_id": pid,
                "title": title,
                "space_key": space_key,
                "labels": labels,
                "version": version,
                "last_modified": last_updated,
                "chunk_index": idx,
                "content_hash": content_hash,
                "url": f"https://api.atlassian.com/ex/confluence/{CONF_CLOUD_ID}/wiki/rest/api/content/{pid}",
            },
        })

    if not docs:
        return 0

    total_sent = 0
    if VECTOR_VARIANT == "dash":
        for start in range(0, len(docs), MAX_CHUNKS_PER_INSERT):
            batch_docs = docs[start:start + MAX_CHUNKS_PER_INSERT]
            chunks_payload = []
            for d in batch_docs:
                md = dict(d.get("metadata", {}))
                md.setdefault("document_id", d["document_id"])  # server expects this in metadata
                chunks_payload.append({
                    "chunk_id": d["document_id"],
                    "content": d["text"],
                    "metadata": md,
                })
            payload = {
                "vector_db_id": COLLECTION_ID,
                "embedding_model_id": EMBEDDING_MODEL_ID,
                "chunks": chunks_payload,
            }
            if VERBOSE:
                print(f"Inserting batch: page {pid} chunks {start}-{start+len(batch_docs)-1}")
            ok = vector_insert_documents(payload, timeout=90)
            if ok is False:
                print("Upsert aborted: vector API not found/enabled", file=sys.stderr)
                raise SystemExit(3)
            total_sent += len(batch_docs)
    elif VECTOR_VARIANT == "underscore":
        for start in range(0, len(docs), MAX_CHUNKS_PER_INSERT):
            batch_docs = docs[start:start + MAX_CHUNKS_PER_INSERT]
            payload = {
                "collection_id": COLLECTION_ID,
                "documents": batch_docs,
            }
            if VERBOSE:
                print(f"Inserting batch: page {pid} docs {start}-{start+len(batch_docs)-1}")
            ok = vector_insert_documents(payload, timeout=90)
            if ok is False:
                print("Upsert aborted: vector API not found/enabled", file=sys.stderr)
                raise SystemExit(3)
            total_sent += len(batch_docs)
    else:
        print("Vector API not discovered; cannot upsert.", file=sys.stderr)
        raise SystemExit(3)
    return total_sent


def main() -> int:
    ensure_env()
    if not discover_vector_api():
        print("Vector API not found in server OpenAPI; aborting.", file=sys.stderr)
        return 2
    ensure_collection()
    total_pages = 0
    total_chunks = 0
    if FILTER_PAGE_IDS:
        for page in fetch_pages_by_ids(FILTER_PAGE_IDS):
            if VERBOSE:
                print(f"Processing page by id: {page.get('id')} '{page.get('title','')}'")
            total_pages += 1
            total_chunks += upsert_chunks(page)
    else:
        if VERBOSE:
            win = f"last {SINCE_HOURS}h" if SINCE_HOURS else "all time"
            print(f"Fetching pages ({win}) with filters: ancestor={FILTER_ANCESTOR_ID or '-'} labels={FILTER_LABELS or '-'} spaces={FILTER_SPACE_KEYS or '-'}")
        for page in fetch_pages_since(SINCE_HOURS):
            if VERBOSE:
                print(f"Processing page: {page.get('id')} '{page.get('title','')}'")
            total_pages += 1
            total_chunks += upsert_chunks(page)
    print(f"Upserted {total_chunks} chunks from {total_pages} pages into collection '{COLLECTION_ID}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


