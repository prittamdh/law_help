"""Pull in only what changed in the AWS dataset since the last run. Safe to run daily.

The dataset rewrites a year's metadata.parquet in place whenever new judgments are
published for it, so "new since last run" means "partitions whose ETag changed".
We remember each partition's ETag in `source_partitions` and re-import only the
partitions that changed; the upsert is keyed on pdf_link, so existing rows (and
their extracted text) are kept.

The Supreme Court bucket works the same way, with one metadata file per year.
"""

import logging
import re
from datetime import date, datetime, timedelta, timezone

import httpx
import psycopg

from . import db, supreme
from .goodlaw import link_treatments
from .importer import (BUCKET_URL, CACHE_DIR, COURT_CODE, SC_UPSERT_SQL, SOURCE, UPSERT_SQL, link_citations,
                       to_records)
from .text import extract_text

log = logging.getLogger("law_help.update")

FIRST_YEAR = 1950
LOCK_ID = 0x1A3_0001  # pg advisory lock, so two scheduled runs never overlap

_CONTENTS = re.compile(
    r"<Key>(?P<key>metadata/parquet/year=(?P<year>\d+)/court=[^/]+/bench=(?P<bench>[^/]+)/metadata\.parquet)</Key>"
    r"<LastModified>(?P<modified>[^<]+)</LastModified><ETag>(?P<etag>[^<]+)</ETag>"
)
_SC_CONTENTS = re.compile(
    r"<Key>(?P<key>metadata/parquet/year=(?P<year>\d+)/metadata\.parquet)</Key>"
    r"<LastModified>(?P<modified>[^<]+)</LastModified><ETag>(?P<etag>[^<]+)</ETag>"
)


def _partition(m: re.Match, bench: str) -> dict:
    return {
        "key": m["key"], "year": int(m["year"]), "bench": bench,
        "etag": m["etag"].replace("&quot;", "").strip('"'),
        "last_modified": datetime.fromisoformat(m["modified"].replace("Z", "+00:00")),
    }


def list_remote(client: httpx.Client, years: list[int]) -> list[dict]:
    """Every Rajasthan metadata partition in the bucket for these years, with its ETag."""
    found = []
    for year in years:
        prefix = f"metadata/parquet/year={year}/court={COURT_CODE}/"
        r = client.get(BUCKET_URL, params={"list-type": "2", "prefix": prefix})
        r.raise_for_status()
        for m in _CONTENTS.finditer(r.text):
            found.append(_partition(m, m["bench"]))
    return found


def list_remote_sc(client: httpx.Client, years: list[int]) -> list[dict]:
    """Every Supreme Court metadata file for these years (one per year), with its ETag."""
    r = client.get(supreme.BUCKET_URL, params={"list-type": "2", "prefix": "metadata/parquet/"})
    r.raise_for_status()
    return [_partition(m, "supreme") for m in _SC_CONTENTS.finditer(r.text) if int(m["year"]) in years]


def download(client: httpx.Client, key: str, bucket: str = BUCKET_URL, cache_dir: str = "") -> bytes:
    r = client.get(f"{bucket}/{key}")
    r.raise_for_status()
    # Refresh the cache too, so `importer metadata` doesn't keep serving the old file.
    cached = CACHE_DIR / cache_dir / key
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(r.content)
    return r.content


def update(client: httpx.Client, conn: psycopg.Connection, years: list[int],
           dry_run: bool = False, court: str = "rajasthan") -> dict:
    """Re-import partitions whose ETag changed. Returns a summary dict.

    court: "rajasthan" (the High Court) or "supreme"."""
    sc = court == "supreme"
    source = supreme.SOURCE if sc else SOURCE
    seen = {row["key"]: row["etag"] for row in conn.execute(
        "SELECT key, etag FROM source_partitions WHERE source = %s", (source,))}
    remote = list_remote_sc(client, years) if sc else list_remote(client, years)
    changed = [p for p in remote if seen.get(p["key"]) != p["etag"]]
    summary = {
        "checked": len(remote), "changed": len(changed), "new": 0, "updated": 0,
        "partitions": [f"{p['year']}/{p['bench']}" for p in changed],
        "dataset_updated": max((p["last_modified"] for p in remote), default=None),
    }
    if dry_run:
        return summary

    for p in sorted(changed, key=lambda p: (p["year"], p["bench"])):
        if sc:
            records = supreme.to_records(download(client, p["key"], supreme.BUCKET_URL, "supreme"), p["year"])
        else:
            records = to_records(download(client, p["key"]), p["year"], p["bench"])
        with conn.cursor() as cur:
            before = cur.execute("SELECT count(*) AS n FROM judgments").fetchone()["n"]
            cur.executemany(SC_UPSERT_SQL if sc else UPSERT_SQL, records)
            new = cur.execute("SELECT count(*) AS n FROM judgments").fetchone()["n"] - before
            cur.execute("""
                INSERT INTO source_partitions (source, key, etag, last_modified, rows, imported_at)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (key) DO UPDATE SET etag = EXCLUDED.etag,
                    last_modified = EXCLUDED.last_modified, rows = EXCLUDED.rows, imported_at = now()
            """, (source, p["key"], p["etag"], p["last_modified"], len(records)))
        conn.commit()  # rows and ETag land together, so a crash just redoes this partition
        summary["new"] += new
        summary["updated"] += len(records) - new
        log.info("%d/%s: %d new, %d already known", p["year"], p["bench"], new, len(records) - new)
    summary["latest_decision"] = conn.execute(
        "SELECT max(decision_date) AS d FROM judgments WHERE source = %s", (source,)).fetchone()["d"]
    return summary


def run(years: list[int] | None, text_limit: int, stale_days: int, dry_run: bool) -> dict:
    years = years or list(range(FIRST_YEAR, date.today().year + 1))
    with httpx.Client(timeout=120) as client, db.connect() as conn:
        db.init_schema(conn)
        if not conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (LOCK_ID,)).fetchone()["ok"]:
            raise SystemExit("another update is already running")
        try:
            summary = update(client, conn, years, dry_run)
            sc = update(client, conn, years, dry_run, court="supreme")
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
            conn.commit()
    for k in ("checked", "changed", "new", "updated"):
        summary[k] += sc[k]
    summary["partitions"] += [f"{p} (Supreme Court)" for p in sc["partitions"]]
    summary["sc_latest_decision"] = sc.get("latest_decision")
    summary["text"] = extract_text(text_limit)["done"] if text_limit and not dry_run else 0
    summary["citations"] = link_citations() if not dry_run else 0
    summary["treatments"] = link_treatments() if not dry_run else 0
    newest = summary["dataset_updated"]
    summary["stale"] = bool(newest and datetime.now(timezone.utc) - newest > timedelta(days=stale_days))
    return summary
