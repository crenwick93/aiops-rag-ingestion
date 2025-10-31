import os
import sys
import time
import re
from typing import List, Optional, Generator

import requests
from markdownify import markdownify as html2md

# ------------------- Simple logging -------------------
LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}
LOG_LEVEL = LEVELS.get(os.getenv("LOG_LEVEL", "INFO").upper(), 20)
ts = lambda: time.strftime("%Y-%m-%dT%H:%M:%S")
def log(level: str, msg: str):
    if LEVELS[level] >= LOG_LEVEL:
        print(f"[{ts()}] {level:5s} | {msg}")
def info(m): log("INFO", m)
def debug(m): log("DEBUG", m)
def warn(m): log("WARN", m)
def error(m): log("ERROR", m)

# ------------------- Llama Stack SDK -------------------
try:
    from llama_stack_client import LlamaStackClient
    from llama_stack_client.types import Document
except Exception:
    print("Install dependency: pip install llama-stack-client", file=sys.stderr)
    raise

# ------------------- Small helpers -------------------
def _as_dict(obj):
    if isinstance(obj, dict): return obj
    if hasattr(obj, "model_dump"): return obj.model_dump()   # pydantic v2
    if hasattr(obj, "dict"): return obj.dict()               # pydantic v1
    return getattr(obj, "__dict__", {}) or {}

def purge_all_vector_dbs(client) -> int:
    vdbs = list(client.vector_dbs.list())
    info(f"Purging all vector DBs (count={len(vdbs)})")
    deleted = 0
    for v in vdbs:
        ident = (_as_dict(v).get("identifier") or _as_dict(v).get("id"))
        if ident:
            info(f" - deleting {ident}")
            client.vector_dbs.unregister(ident)  # Llama-Stack 0.2.x delete
            deleted += 1
    return deleted

def conf_session(user: str, token: str) -> requests.Session:
    s = requests.Session()
    s.auth = (user, token)
    s.headers.update({"Accept": "application/json"})
    return s

def resolve_space_key_by_name(session: requests.Session, cloud_id: str, space_name: str) -> Optional[str]:
    base = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/rest/api"
    url = f"{base}/space"
    start = 0
    while True:
        r = session.get(url, params={"start": start, "limit": 50}, timeout=60)
        r.raise_for_status()
        results = r.json().get("results", [])
        for sp in results:
            if str(sp.get("name", "")).strip().lower() == space_name.strip().lower():
                return sp.get("key")
        if len(results) < 50: break
        start += len(results)
    return None

def build_cql(space_key: str, labels: List[str], since_hours: int) -> str:
    parts = ["type=page"]
    if since_hours > 0: parts.append(f'lastmodified > now("-{since_hours}h")')
    if space_key: parts.append(f'space="{space_key}"')
    if labels: parts.append("(" + " OR ".join([f'label="{l}"' for l in labels]) + ")")
    return " and ".join(parts)

def conf_search_pages(session: requests.Session, cloud_id: str, cql: str, limit: int = 50) -> Generator[dict, None, None]:
    base = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/rest/api"
    url = f"{base}/content/search"
    start = 0
    while True:
        r = session.get(
            url,
            params={
                "cql": cql,
                "limit": limit,
                "start": start,
                "expand": "body.export_view,version,metadata.labels,space,history.lastUpdated",
            },
            timeout=60,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results: break
        for item in results: yield item
        if len(results) < limit: break
        start += len(results)

def html_to_markdown(html: str) -> str:
    md = html2md(html or "", strip=["script", "style"])
    md = re.sub(r"\s+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()

# ------------------- Main -------------------
def main() -> int:
    llama_base_url = os.getenv("LLAMA_BASE_URL", "http://lsd-llama-milvus-inline-service.default.svc.cluster.local:8321").rstrip("/")
    conf_cloud_id = os.getenv("CONF_CLOUD_ID", "").strip()
    conf_user = os.getenv("CONF_USER", "").strip()
    conf_api_token = os.getenv("CONF_API_TOKEN", "").strip()
    space_name = os.getenv("SPACE_NAME", "").strip()
    labels = [s.strip() for s in os.getenv("LABELS", "").split(",") if s.strip()]
    since_hours = int(os.getenv("SINCE_HOURS", "0") or 0)
    vector_db_name = os.getenv("VECTOR_DB_ID", "confluence").strip()
    batch_size = int(os.getenv("BATCH_SIZE", "100") or 100)

    missing = [n for n, v in [("CONF_CLOUD_ID", conf_cloud_id), ("CONF_USER", conf_user), ("CONF_API_TOKEN", conf_api_token), ("SPACE_NAME", space_name)] if not v]
    if missing:
        error("Missing env: " + ", ".join(missing)); return 2

    info(f"LLAMA_BASE_URL: {llama_base_url}")
    info(f"VECTOR_DB_ID:   {vector_db_name}")
    info(f"SPACE_NAME:     {space_name}")

    # Connect & reset vector DBs
    info("Connecting to Llama Stack...")
    client = LlamaStackClient(base_url=llama_base_url)
    purged = purge_all_vector_dbs(client)
    info(f"Purged {purged} vector DB(s)")

    # Create fresh DB
    info("Selecting embedding model...")
    embed_model = next(m for m in client.models.list() if m.model_type == "embedding")
    info(f"Using embedding model: {embed_model.identifier}")
    info(f"Registering new vector DB '{vector_db_name}'...")
    vdb = client.vector_dbs.register(vector_db_id=vector_db_name, embedding_model=embed_model.identifier)
    vdb_id = _as_dict(vdb).get("identifier") or _as_dict(vdb).get("id")
    info(f"Created vector DB: {vdb_id}")

    # Confluence ingest
    info("Creating Confluence session...")
    session = conf_session(conf_user, conf_api_token)
    info(f"Resolving space key for '{space_name}'...")
    space_key = resolve_space_key_by_name(session, conf_cloud_id, space_name)
    if not space_key:
        error(f"Space '{space_name}' not found or no access."); return 3
    info(f"SPACE_KEY: {space_key}")

    cql = build_cql(space_key, labels, since_hours)
    info(f"CQL: {cql}")
    info(f"BATCH_SIZE: {batch_size}")

    documents: List[Document] = []
    prepared = inserted = 0

    info("Fetching Confluence pages...")
    for page in conf_search_pages(session, conf_cloud_id, cql):
        page_id = page.get("id"); title = page.get("title", "")
        body_html = (((page.get("body") or {}).get("export_view") or {}).get("value")) or ""
        md = html_to_markdown(body_html)
        if not md: continue
        doc = Document(
            document_id=f"conf-{page_id}",
            content=md,
            mime_type="text/markdown",
            metadata={
                "source": "confluence",
                "source_url": f"https://api.atlassian.com/ex/confluence/{conf_cloud_id}/wiki/rest/api/content/{page_id}",
                "title": title,
                "space_key": (page.get("space") or {}).get("key") or "",
            },
        )
        documents.append(doc); prepared += 1

        if len(documents) >= batch_size:
            info(f"Inserting batch of {len(documents)} into {vdb_id} ...")
            client.tool_runtime.rag_tool.insert(documents=documents, vector_db_id=vdb_id, chunk_size_in_tokens=512)
            inserted += len(documents); documents.clear()

    info(f"Prepared {prepared} page(s)")
    if documents:
        info(f"Inserting final batch of {len(documents)} into {vdb_id} ...")
        client.tool_runtime.rag_tool.insert(documents=documents, vector_db_id=vdb_id, chunk_size_in_tokens=512)
        inserted += len(documents)

    if inserted: info(f"Inserted {inserted} document(s) into {vdb_id}")
    else:        warn("No documents inserted (check filters/CQL).")

    info("Done.")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        warn("Interrupted."); raise SystemExit(130)
