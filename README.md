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
  python sync.py
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
- Optional but recommended if you host the Vector API in-cluster and it uses file-backed storage: a PersistentVolumeClaim (PVC). See `openshift/pvc.yaml` for an example. Bind/mount this PVC in your Vector API deployment, not in this sync job.

### Quick credential/connectivity checks (before deploying)
- Confluence credentials and space access (requires valid CONF_*):
  ```bash
  export CONF_CLOUD_ID=84927973-adf1-4112-be18-59ea4f9c3d60
  export CONF_USER=your-email@example.com
  export CONF_API_TOKEN=your-confluence-api-token
  # Check you can access the target space by name (defaults to SPACE_NAME="Known Issues")
  export SPACE_NAME="Known Issues"
  bash ./check_api_access_to_space.sh
  # Optional: locate a parent page id ("folder") by title if you prefer folder scoping
  # bash ./get_folder_id.sh "Known Issues"
  ```

- Vector API connectivity (requires VECTOR_API_BASE_URL):
  ```bash
  export VECTOR_API_BASE_URL=http://vector-api:8001
  # Calls the Vector API OpenAPI, registers a test collection, inserts a couple chunks, then queries
  bash ./test_sync.sh
  ```
  Note: The script discovers the API variant automatically and may create a temporary test collection.

## Files
- `Containerfile`: Python 3.12 slim image
- `requirements.txt`: requests, beautifulsoup4, markdownify
- `sync.py`: fetches pages updated in the last N hours, converts to Markdown, chunks, and upserts to the Vector API (auto-discovers endpoints)
- `openshift/secret.yaml`: Confluence credentials (edit values before applying)
- `openshift/cronjob.yaml`: Nightly sync job (edit image, URLs, env as needed)

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
  - `FILTER_FOLDER_ID` (alternative scoping): parent page id (often called "folder") to ingest its descendants. Obtain via `get_folder_id.sh` or Confluence UI "Copy link".

### Ingest an entire space
1) Run `check_api_access_to_space.sh` with `SPACE_NAME` to confirm access and get the space key.
2) Set `FILTER_SPACE_KEYS=<SPACE_KEY>` in your env/ConfigMap.
3) For initial full ingestion, set `SINCE_HOURS=""` (empty) or a large window (e.g., `720`).

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
