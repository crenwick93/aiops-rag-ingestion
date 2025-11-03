# Confluence → Vector DB Ingestion (OpenShift-friendly, vendor-neutral)

## Podman Quickstart
Build, test locally, and (optionally) push the sync image.

1. Build the image:
```bash
podman build -t confluence-sync:latest .
```

2. Run locally to test (set your envs):
```bash
podman run --rm \
  -e CONF_CLOUD_ID=84927973-adf1-4112-be18-59ea4f9c3d60 \
  -e CONF_USER=your-email@example.com \
  -e CONF_API_TOKEN=your-confluence-api-token \
  -e VECTOR_API_BASE_URL=http://vector-api:8001 \
  -e COLLECTION_ID=confluence-known-issues-v1 \
  localhost/confluence-sync:latest
```

3. Push to a registry (example: Quay):
```bash
podman login quay.io
podman tag confluence-sync:latest quay.io/<your-namespace>/confluence-sync:latest
podman push quay.io/<your-namespace>/confluence-sync:latest
```

### Local environment and venv
- Load environment variables from `.env` (created from `.env.example`):
  ```bash
  set -a
  source .env
  set +a
  ```
- Virtual environment is optional if you run via container. If running Python directly, a venv is recommended:
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -r requirements.txt
  pip install "llama-stack-client==0.2.23"
  python ingest_conf.py
  ```

This repo provides a tiny service to ingest Confluence pages into a vector database via a generic Vector API (auto-discovers two common variants). It includes simple OpenShift assets for running as a CronJob. No dependency on a specific "llama" stack is required.

Best practices implemented:
- Preserve structure via HTML→Markdown conversion
- Chunk ~1,000 tokens with ~180 token overlap
- Stable chunk IDs with content hash for dedup/incremental updates
- Incremental upserts into a named collection (default: `confluence`)

## Prerequisites (OpenShift)
- A running Vector API endpoint reachable from the cluster, exposing either:
  - `/v1/vector-io/insert` and `/v1/vector-dbs` ("dash" variant), or
  - `/v1/vector_io/documents` and `/v1/vector_io/collections` ("underscore" variant).
- Network access from the CronJob pod to the Vector API service.
- Confluence Cloud credentials (email + API token).
- Optional but recommended if you host the Vector API in-cluster and it uses file-backed storage: a PersistentVolumeClaim (PVC). Define and mount the PVC in your Vector API deployment (in that repo), not in this sync job. This repo does not ship a PVC manifest.

### Quick credential/connectivity checks (before deploying)
- Confluence credentials and space access (requires valid CONF_*):
  ```bash
  export CONF_CLOUD_ID=84927973-adf1-4112-be18-59ea4f9c3d60
  export CONF_USER=your-email@example.com
  export CONF_API_TOKEN=your-confluence-api-token
  # Check you can access the target space by name (defaults to SPACE_NAME="Known Issues")
  export SPACE_NAME="Known Issues"
  # Optional: locate a parent page id ("folder") by using Confluence UI → Copy link
  ```

### Quick Confluence connectivity test (Python)
Use the helper script to verify credentials and list pages from a space before running ingestion:
```bash
export CONF_CLOUD_ID=...
export CONF_USER=...
export CONF_API_TOKEN=...
export SPACE_NAME="Known Issues"
# optional
export TEST_LIMIT=10

python test_confluence.py
```

### Notebook option: `ingest_conf.ipynb`
Prefer an interactive check? Open the notebook to run the same ingestion steps cell-by-cell:
```bash
pip install -r requirements.txt && pip install llama-stack-client jupyter
export LLAMA_BASE_URL=...
export CONF_CLOUD_ID=...
export CONF_USER=...
export CONF_API_TOKEN=...
export SPACE_NAME="Known Issues"

jupyter notebook ingest_conf.ipynb
```
The notebook mirrors `ingest_conf.py` and is useful for quick experimentation before containerizing or scheduling.

Note for notebook users
- Create a `.env` file in the project root containing at least `CONF_CLOUD_ID`, `CONF_USER`, and `CONF_API_TOKEN` (and optionally `SPACE_NAME`).
- In the first cell, load it with python-dotenv before running the rest:
  ```python
  %pip install -q python-dotenv
  from dotenv import load_dotenv; load_dotenv('.env')
  ```

## Files
- `Containerfile`: Python 3.12 slim image
- `requirements.txt`: requests, beautifulsoup4, markdownify
- `sync.py`: fetches pages updated in the last N hours, converts to Markdown, chunks, and upserts to the Vector API (auto-discovers endpoints)
- `openshift/secret.yaml`: Confluence credentials (edit values before applying)
- `openshift/cronjob.yaml`: Nightly sync job (edit image, URLs, env as needed)

## PoC demo (Llama Stack) — use `ingest_conf.py`
For the Proof of Concept demo, use `ingest_conf.py`. It ingests Confluence pages directly into Llama Stack via the `rag-tool` insert route and mirrors the working notebook’s ingestion flow.

Quick start:
```bash
pip install -r requirements.txt && pip install llama-stack-client
export LLAMA_BASE_URL=http://<llama-stack-host>:8321
export CONF_CLOUD_ID=...
export CONF_USER=...
export CONF_API_TOKEN=...
export SPACE_NAME="Known Issues"
# optional
export VECTOR_DB_ID="conf-known-issues"
export LABELS=""
export SINCE_HOURS=24
python ingest_conf.py
```

If you see HTTP 426 (client/server version mismatch):
- Your server reports 0.2.x; pin the client locally to a compatible 0.2.x:
  ```bash
  pip install "llama-stack-client==0.2.23"
  ```
- The Containerfile pins `llama-stack-client==0.2.23`. Rebuild/push and redeploy the image.

Notes for nightly runs (demo):
- Persist the working directory so `conf_known_issues.vdb` is retained; this lets the script reuse the same vector DB identifier each night.
- Batching and HTTP retries are enabled for robustness. Adjust `BATCH_SIZE` if needed.

Important PoC caveats (not production-hardened):
- Duplicates on re-ingest: On your current Llama Stack build, `rag-tool/insert` behaves as append (not upsert). Re-inserting a page with the same `document_id` can create duplicates over time.
- Deletions not handled: If a Confluence page is deleted, its vectors remain in the DB; this PoC does not prune them.

Production guidance:
- Prefer an upsert or delete capability (when available) or implement a rotation strategy (new vector DB per run and point inference to the latest).
- Track and reconcile deletions (e.g., compare known `document_id`s and remove those missing upstream).
- Use `SINCE_HOURS` for incremental ingestion and persist vector DB state reliably.

### Minimal environment (.env) for `ingest_conf.py`
Required:
- `LLAMA_BASE_URL`
- `CONF_CLOUD_ID`
- `CONF_USER`
- `CONF_API_TOKEN`
- `SPACE_NAME`

Optional:
- `VECTOR_DB_ID` (default: `conf-known-issues`)
- `LABELS` (comma-separated)
- `SINCE_HOURS` (default: `0`; for nightly set `24`)
- `BATCH_SIZE` (default: `100`)



### Containerize and run on OpenShift (Job/CronJob)
Build and push a demo image (note: entrypoint runs `ingest_conf.py`):
```bash
podman build -t your-registry/your-namespace/aiops-conf-ingestion:latest .
podman push your-registry/your-namespace/aiops-conf-ingestion:latest
```

Create/OpenShift resources (envsubst-driven templates):
```bash
# Prepare environment for templating
export NAMESPACE=<your-namespace>
export IMAGE=your-registry/your-namespace/aiops-conf-ingestion:latest
export CRON_SCHEDULE="0 2 * * *"

# Llama + Confluence settings (can be sourced from .env)
export LLAMA_BASE_URL=${LLAMA_BASE_URL}
export CONF_CLOUD_ID=${CONF_CLOUD_ID}
export SPACE_NAME=${SPACE_NAME:-"Known Issues"}
export VECTOR_DB_ID=${VECTOR_DB_ID:-"conf-known-issues"}
export LABELS=${LABELS:-""}
export SINCE_HOURS=${SINCE_HOURS:-"24"}
export BATCH_SIZE=${BATCH_SIZE:-"100"}
export CONF_USER=${CONF_USER}
export CONF_API_TOKEN=${CONF_API_TOKEN}

# Apply templated resources
envsubst < openshift/secret.yaml | oc apply -f -
envsubst < openshift/configmap-llama.yaml | oc apply -f -
envsubst < openshift/job-llama.yaml | oc apply -f -
# Or schedule nightly CronJob
envsubst < openshift/cronjob-llama.yaml | oc apply -f -
```

Replace the image reference `your-registry/your-namespace/aiops-conf-ingestion:latest` in the YAMLs with your registry path. By default, these manifests run ephemerally (no PVC). For PoC/demo this is fine; each run will recreate or reuse the vector DB per server behavior. If you want persistence of the `.vdb` file across pods, add a PVC and mount it to `/app` in the Job/CronJob.

Environment keys used by the container:
- From `openshift/configmap-llama.yaml`: `LLAMA_BASE_URL`, `CONF_CLOUD_ID`, `SPACE_NAME`, `VECTOR_DB_ID`, `LABELS`, `SINCE_HOURS`, `BATCH_SIZE`
- From `openshift/secret.yaml`: `CONF_USER`, `CONF_API_TOKEN`

### OpenShift Template (recommended one-command apply)
Render and apply the template with your current shell env (no file splitting):
```bash
oc process -f openshift/template.ocp \
  -p IMAGE=your-registry/your-namespace/aiops-conf-ingestion:latest \
  -p CRON_SCHEDULE="0 2 * * *" \
  -p LLAMA_BASE_URL="$LLAMA_BASE_URL" \
  -p CONF_CLOUD_ID="$CONF_CLOUD_ID" \
  -p SPACE_NAME="${SPACE_NAME:-Known Issues}" \
  -p VECTOR_DB_ID="${VECTOR_DB_ID:-conf-known-issues}" \
  -p LABELS="${LABELS:-}" \
  -p BATCH_SIZE="${BATCH_SIZE:-100}" \
  -p CONF_USER="$CONF_USER" \
  -p CONF_API_TOKEN="$CONF_API_TOKEN" \
| oc apply -f -
```
This creates/updates the ConfigMap, Secret, one-off Job and nightly CronJob in a single command.
The one-off Job starts ingestion immediately after apply; the CronJob will schedule future nightly runs.

### Demo reset strategy — recreate the vector DB each run
For PoC simplicity (and to avoid duplicate documents on append-only servers), run with a clean vector DB each time:
- Before each run, delete any existing vector DB(s) for your logical name (e.g., `VECTOR_DB_ID=confluence`).
- Then start the job; the script will create a fresh DB and ingest the current pages.

How to delete (choose one):
- Using the CLI:
  ```bash
  llama-stack-client vector_dbs list
  # Note the identifier (e.g., vs_abc123...) for your target DB name (vector_db_name: confluence)
  llama-stack-client vector_dbs delete vs_abc123...
  ```
- Using the API (if DELETE is supported by your server):
  ```bash
  BASE="$LLAMA_BASE_URL" 
  ID="vs_abc123..."   # identifier from vector_dbs list
  curl -s -X DELETE "$BASE/v1/vector-dbs/$ID"
  ```

Why this approach?
- It guarantees a deterministic demo state and avoids accumulating duplicates when inserts are not upsert.
- In production, prefer an incremental pipeline: fetch only changes, upsert (or delete then insert), and reconcile deletions.

Use your local .env to drive the OpenShift ConfigMap/Secret
If you prefer to source values directly from your `.env` instead of editing YAMLs, create resources from the files and keep the same names the templates expect (`working-sync-config`, `confluence-creds`):
```bash
# Create/replace ConfigMap from .env (non-secret values)
oc create configmap working-sync-config \
  --from-env-file=.env \
  -o yaml --dry-run=client | oc apply -f -

# Create/replace Secret from a separate secrets file (.env.secrets) or literals
# Option A: from a file with CONF_USER and CONF_API_TOKEN lines
oc create secret generic confluence-creds \
  --from-env-file=.env.secrets \
  -o yaml --dry-run=client | oc apply -f -
# Option B: from explicit literals
oc create secret generic confluence-creds \
  --from-literal=CONF_USER="$CONF_USER" \
  --from-literal=CONF_API_TOKEN="$CONF_API_TOKEN" \
  -o yaml --dry-run=client | oc apply -f -

# Then apply the Job/CronJob (they already envFrom this ConfigMap and Secret)
oc apply -f openshift/job-llama.yaml
# or
oc apply -f openshift/cronjob-llama.yaml
```

## Build and push with podman
From this directory:
```bash
# Option A: External registry (e.g. Quay)
podman build -t quay.io/<your-namespace>/confluence-sync:latest .
podman push quay.io/<your-namespace>/confluence-sync:latest

# Option B: OpenShift internal registry (replace `<ns>` with your namespace)
podman login --tls-verify=false image-registry.openshift-image-registry.svc:5000
podman build -t image-registry.openshift-image-registry.svc:5000/<ns>/confluence-sync:latest .
podman push --tls-verify=false image-registry.openshift-image-registry.svc:5000/<ns>/confluence-sync:latest
```

### Building from macOS (arm64) for OpenShift (x86_64)
OpenShift nodes commonly run x86_64. If you are on Apple Silicon (arm64), build the image for linux/amd64:

```bash
# Podman (recommended on macOS)
podman build --platform=linux/amd64 -t quay.io/<ns>/confluence-sync:latest .
podman push quay.io/<ns>/confluence-sync:latest

# OpenShift internal registry via Podman
podman build --platform=linux/amd64 -t image-registry.openshift-image-registry.svc:5000/<ns>/confluence-sync:latest .
podman push --tls-verify=false image-registry.openshift-image-registry.svc:5000/<ns>/confluence-sync:latest

# Docker Buildx alternative
docker buildx create --use || true
docker buildx build --platform linux/amd64 -t quay.io/<ns>/confluence-sync:latest --push .
```

Note: Specifying `--platform=linux/amd64` ensures the image runs on x86_64 OpenShift nodes.

## Deploy on OpenShift
Edit `openshift/secret.yaml`, `openshift/configmap.yaml` and `openshift/cronjob.yaml` first (image, URLs), then:
```bash
# Apply into your current project
oc apply -f openshift/secret.yaml
oc apply -f openshift/configmap.yaml
oc apply -f openshift/cronjob.yaml

# Or target a specific namespace
oc apply -n <ns> -f openshift/secret.yaml
oc apply -n <ns> -f openshift/configmap.yaml
oc apply -n <ns> -f openshift/cronjob.yaml

# If the namespace does not exist yet
oc new-project <ns>
oc apply -n <ns> -f openshift/
```
Defaults:
- schedule: nightly 02:00
- vector API base: `http://vector-api:8001`
- collection: `confluence`
- embedding: `all-MiniLM-L6-v2`
- chunking: 1000 tokens, 180 overlap

## Required environment
Set via `env`/`envFrom` in the CronJob:
- `CONF_CLOUD_ID` (required): your site cloud id
- `CONF_USER` and `CONF_API_TOKEN` (via Secret)
- `VECTOR_API_BASE_URL` (no default; required)
- Optional:
  - `COLLECTION_ID` (default: confluence)
  - `EMBEDDING_MODEL_ID` (default: all-MiniLM-L6-v2)
  - `SINCE_HOURS` (default: 24)
  - `CHUNK_TOKENS` (default: 1000)
  - `CHUNK_OVERLAP` (default: 180)
  - `FILTER_SPACE_KEYS` (recommended to ingest an entire space): set to the space key (e.g., `KNOWN`). Use `check_api_access_to_space.sh` to validate visibility and find the key.
  - `FILTER_FOLDER_ID` (alternative scoping): parent page id (often called "folder") to ingest its descendants. Obtain via Confluence UI "Copy link".

### Ingest an entire space
1) Set `SPACE_NAME` to your target space; the tools resolve the space key automatically. Optionally run `test_confluence.py` to verify access and list a few pages.
2) (Optional, legacy flow) If using filters with other scripts, set `FILTER_SPACE_KEYS=<SPACE_KEY>`.
3) For initial full ingestion, use the default all-time behavior (or set a large lookback window in legacy flows).

### Cloud ID and service account note
- Some organizations require using the Atlassian API gateway (`api.atlassian.com`) with a `cloudId` instead of the site URL.
- Obtain your cloud id from Atlassian admin pages. When set, this repo uses:
  - `https://api.atlassian.com/ex/confluence/<cloudId>/wiki/rest/api`
- Create a service account with Confluence product access and generate an API token under that account. Ensure it has permission to view the target spaces.

## Verify ingestion
Query the collection directly:
```bash
curl -sS -X POST "$VECTOR_API_BASE_URL/v1/vector_io/query" \
  -H 'content-type: application/json' \
  -d '{
    "collection_id": "confluence",
    "query": "Summarize the main points from our Confluence docs",
    "top_k": 3
  }' | jq .
```

## Notes
- Keep credentials out of source control. Use `openshift/secret.yaml` or `oc create secret`.
- Ensure your Vector API server stores vectors persistently per its documentation.
- For deletions, add a periodic reconcile job that removes documents for pages that no longer exist.
