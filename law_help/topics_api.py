"""Topics API: /api/topics, /api/topics/{slug}, and the /topics page (law_help.topics)."""

import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from . import db, topics

router = APIRouter()

TOPIC_LIMIT = 10
# Counting a big topic (every writ petition) takes a while on a million rows, and the
# counts only change when `update` runs, so they are kept for a few minutes.
CACHE_SECONDS = 600
_cache: dict[str, tuple[float, object]] = {}


def clear_cache() -> None:
    _cache.clear()


def _cached(key: str, fn):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = fn()
    _cache[key] = (time.monotonic(), value)
    return value


def get_conn():
    with db.connect() as conn:
        yield conn


def _topic(slug: str) -> dict:
    topic = topics.get(slug)
    if not topic:
        raise HTTPException(404, "no such topic")
    return topic


@router.get("/topics", include_in_schema=False)
def topics_page(topic: str | None = None):
    from .api import STATIC_DIR
    return FileResponse(STATIC_DIR / "topics.html")


@router.get("/api/topics")
def list_topics(counts: bool = Query(True, description="also count each topic's judgments"),
                conn=Depends(get_conn)):
    def count(t):
        where, params = topics.topic_sql(t)
        return conn.execute(f"SELECT count(*) AS n FROM judgments WHERE {where}", params).fetchone()["n"]
    return [{"slug": t["slug"], "name": t["name"], "blurb": t["blurb"],
             "total": _cached(f"count:{t['slug']}", lambda: count(t)) if counts else None}
            for t in topics.TOPICS]


@router.get("/api/topics/{slug}")
def get_topic(slug: str, limit: int = Query(TOPIC_LIMIT, ge=1, le=50), conn=Depends(get_conn)):
    """A topic's rules, counts by court and year, its most cited and its latest judgments."""
    from .api import LIST_COLUMNS, _present
    topic = _topic(slug)
    where, params = topics.topic_sql(topic)

    def counts():
        by_bench = conn.execute(
            f"SELECT bench, count(*) AS n FROM judgments WHERE {where} GROUP BY 1 ORDER BY 2 DESC", params).fetchall()
        by_year = conn.execute(
            f"SELECT extract(year FROM decision_date)::int AS year, count(*) AS n FROM judgments "
            f"WHERE {where} AND decision_date IS NOT NULL GROUP BY 1 ORDER BY 1 DESC LIMIT 10", params).fetchall()
        return {"total": sum(r["n"] for r in by_bench), "by_bench": by_bench, "by_year": by_year}

    # Most cited: only judgments some later judgment cites (cited_counts is small, so narrow to it first).
    most_cited = conn.execute(
        f"SELECT {LIST_COLUMNS} FROM judgments WHERE id IN (SELECT judgment_id FROM cited_counts) AND {where} "
        "ORDER BY cited_by_count DESC, decision_date DESC NULLS LAST, id DESC LIMIT %(limit)s",
        {**params, "limit": limit}).fetchall()
    latest = conn.execute(
        f"SELECT {LIST_COLUMNS} FROM judgments WHERE {where} "
        "ORDER BY decision_date DESC NULLS LAST, id DESC LIMIT %(limit)s",
        {**params, "limit": limit}).fetchall()
    return {"slug": slug, "name": topic["name"], "blurb": topic["blurb"], "rules": topics.describe(topic),
            **_cached(f"detail:{slug}", counts),
            "most_cited": [_present(r) for r in most_cited], "latest": [_present(r) for r in latest]}
