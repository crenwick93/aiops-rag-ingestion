import os
import sys
import requests


def conf_session(user: str, token: str) -> requests.Session:
    s = requests.Session()
    s.auth = (user, token)
    s.headers.update({"Accept": "application/json"})
    return s


def resolve_space_key(session: requests.Session, cloud_id: str, space_name: str) -> str:
    base = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/rest/api"
    url = f"{base}/space"
    start = 0
    while True:
        r = session.get(url, params={"start": start, "limit": 50}, timeout=60)
        r.raise_for_status()
        results = r.json().get("results", [])
        for sp in results:
            if str(sp.get("name", "")).strip().lower() == space_name.strip().lower():
                return sp.get("key") or ""
        if len(results) < 50:
            break
        start += len(results)
    return ""


def list_pages(session: requests.Session, cloud_id: str, space_key: str, limit: int) -> None:
    base = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/rest/api"
    url = f"{base}/content/search"
    cql = f'type=page and space="{space_key}"'
    start = 0
    fetched = 0
    while fetched < limit:
        page_limit = min(50, limit - fetched)
        r = session.get(
            url,
            params={
                "cql": cql,
                "limit": page_limit,
                "start": start,
                "expand": "history.lastUpdated",
            },
            timeout=60,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            break
        for item in results:
            pid = item.get("id")
            title = item.get("title", "")
            updated = (((item.get("history") or {}).get("lastUpdated") or {}).get("when")) or ""
            print(f"- {pid}: {title} ({updated})")
        fetched += len(results)
        if len(results) < page_limit:
            break
        start += len(results)


def main() -> int:
    cloud_id = os.getenv("CONF_CLOUD_ID", "").strip()
    user = os.getenv("CONF_USER", "").strip()
    token = os.getenv("CONF_API_TOKEN", "").strip()
    space_name = os.getenv("SPACE_NAME", "").strip()
    limit = int(os.getenv("TEST_LIMIT", "10") or 10)

    missing = [n for n, v in [("CONF_CLOUD_ID", cloud_id), ("CONF_USER", user), ("CONF_API_TOKEN", token), ("SPACE_NAME", space_name)] if not v]
    if missing:
        print("Missing env: " + ", ".join(missing), file=sys.stderr)
        return 2

    print(f"Checking Confluence access for space '{space_name}' (limit={limit}) …")
    s = conf_session(user, token)
    key = resolve_space_key(s, cloud_id, space_name)
    if not key:
        print(f"Space '{space_name}' not found or no access.", file=sys.stderr)
        return 3
    print(f"SPACE_KEY: {key}")
    print("Listing pages:")
    list_pages(s, cloud_id, key, limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


