"""Pull in only what changed in the AWS dataset since the last run. Safe to run daily.

The dataset rewrites a year's metadata.parquet in place whenever new judgments are
published for it, so "new since last run" means "partitions whose ETag changed".
We remember each partition's ETag in `source_partitions` and re-import only the
partitions that changed; the upsert is keyed on pdf_link, so existing rows (and
their extracted text) are kept.
"""

import logging
import re
from datetime import date, datetime, timedelta, timezone

import httpx
import psycopg

from . import db
from .importer import BUCKET_URL, CACHE_DIR, COURT_CODE, SOURCE, UPSERT_SQL, extract_text, to_records

log = logging.getLogger("law_help.update")

FIRST_YEAR = 1950
LOCK_ID = 0x1A3_0001  # pg advisory lock, so two scheduled runs never overlap

_CONTENTS = re.compile(
    r"<Key>(?P<key>metadata/parquet/year=(?P<year>\d+)/court=[^/]+/bench=(?P<bench>[^/]+)/metadata\.parquet)</Key>"
    r"<LastModified>(?P<modified>[^<]+)</LastModified><ETag>(?P<etag>[^<]+)</ETag>"
)


def list_remote(client: httpx.Client, years: list[int]) -> list[dict]:
    """Every Rajasthan metadata partition in the bucket for these years, with its ETag."""
    found = []
    for year in years:
        prefix = f"metadata/parquet/year={year}/court={COURT_CODE}/"
        r = client.get(BUCKET_URL, params={"list-type": "2", "prefix": prefix})
        r.raise_for_status()
        for m in _CONTENTS.finditer(r.text):
            found.append({
                "key": m["key"], "year": int(m["year"]), "bench": m["bench"],
                "etag": m["etag"].replace("&quot;", "").strip('"'),
                "last_modified": datetime.fromisoformat(m["modified"].replace("Z", "+00:00")),
            })
    return found


def download(client: httpx.Client, key: str) -> bytes:
    r = client.get(f"{BUCKET_URL}/{key}")
    r.raise_for_status()
    # Refresh the cache too, so `importer metadata` doesn't keep serving the old file.
    cached = CACHE_DIR / key
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(r.content)
    return r.content


def update(client: httpx.Client, conn: psycopg.Connection, years: list[int],
           dry_run: bool = False) -> dict:
    """Re-import partitions whose ETag changed. Returns a summary dict."""
    seen = {row["key"]: row["etag"] for row in conn.execute(
        "SELECT key, etag FROM source_partitions WHERE source = %s", (SOURCE,))}
    remote = list_remote(client, years)
    changed = [p for p in remote if seen.get(p["key"]) != p["etag"]]
    summary = {
        "checked": len(remote), "changed": len(changed), "new": 0, "updated": 0,
        "partitions": [f"{p['year']}/{p['bench']}" for p in changed],
        "dataset_updated": max((p["last_modified"] for p in remote), default=None),
    }
    if dry_run:
        return summary

    for p in sorted(changed, key=lambda p: (p["year"], p["bench"])):
        records = to_records(download(client, p["key"]), p["year"], p["bench"])
        with conn.cursor() as cur:
            before = cur.execute("SELECT count(*) AS n FROM judgments").fetchone()["n"]
            cur.executemany(UPSERT_SQL, records)
            new = cur.execute("SELECT count(*) AS n FROM judgments").fetchone()["n"] - before
            cur.execute("""
                INSERT INTO source_partitions (source, key, etag, last_modified, rows, imported_at)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (key) DO UPDATE SET etag = EXCLUDED.etag,
                    last_modified = EXCLUDED.last_modified, rows = EXCLUDED.rows, imported_at = now()
            """, (SOURCE, p["key"], p["etag"], p["last_modified"], len(records)))
        conn.commit()  # rows and ETag land together, so a crash just redoes this partition
        summary["new"] += new
        summary["updated"] += len(records) - new
        log.info("%d/%s: %d new, %d already known", p["year"], p["bench"], new, len(records) - new)
    summary["latest_decision"] = conn.execute(
        "SELECT max(decision_date) AS d FROM judgments WHERE source = %s", (SOURCE,)).fetchone()["d"]
    return summary


def run(years: list[int] | None, text_limit: int, stale_days: int, dry_run: bool) -> dict:
    years = years or list(range(FIRST_YEAR, date.today().year + 1))
    with httpx.Client(timeout=120) as client, db.connect() as conn:
        db.init_schema(conn)
        if not conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (LOCK_ID,)).fetchone()["ok"]:
            raise SystemExit("another update is already running")
        try:
            summary = update(client, conn, years, dry_run)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
            conn.commit()
    summary["text"] = extract_text(text_limit) if text_limit and not dry_run else 0
    newest = summary["dataset_updated"]
    summary["stale"] = bool(newest and datetime.now(timezone.utc) - newest > timedelta(days=stale_days))
    return summary
