"""Topics API: /api/topics, /api/topics/{slug}, and the /topics page (law_help.topics)."""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from . import counts as stored, db, topics

router = APIRouter()

TOPIC_LIMIT = 10


def clear_cache() -> None:
    """Forget the stored counts (law_help.counts), e.g. in tests after changing judgments."""
    with db.connect() as conn:
        stored.clear(conn)


def count_topic(conn, t: dict) -> int:
    where, params = topics.topic_sql(t)
    return conn.execute(f"SELECT count(*) AS n FROM judgments WHERE {where}", params).fetchone()["n"]


def topic_counts(conn, t: dict) -> dict:
    """A topic's total and its counts by court and by year."""
    where, params = topics.topic_sql(t)
    by_bench = conn.execute(
        f"SELECT bench, count(*) AS n FROM judgments WHERE {where} GROUP BY 1 ORDER BY 2 DESC", params).fetchall()
    by_year = conn.execute(
        f"SELECT extract(year FROM decision_date)::int AS year, count(*) AS n FROM judgments "
        f"WHERE {where} AND decision_date IS NOT NULL GROUP BY 1 ORDER BY 1 DESC LIMIT 10", params).fetchall()
    return {"total": sum(r["n"] for r in by_bench), "by_bench": by_bench, "by_year": by_year}


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
    return [{"slug": t["slug"], "name": t["name"], "blurb": t["blurb"],
             "total": stored.cached(conn, f"topic:count:{t['slug']}", lambda: count_topic(conn, t))
             if counts else None}
            for t in topics.TOPICS]


@router.get("/api/topics/{slug}")
def get_topic(slug: str, limit: int = Query(TOPIC_LIMIT, ge=1, le=50), conn=Depends(get_conn)):
    """A topic's rules, counts by court and year, its most cited and its latest judgments."""
    from .api import LIST_COLUMNS, _present
    topic = _topic(slug)
    where, params = topics.topic_sql(topic)


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
            **stored.cached(conn, f"topic:detail:{slug}", lambda: topic_counts(conn, topic)),
            "most_cited": [_present(r) for r in most_cited], "latest": [_present(r) for r in latest]}
