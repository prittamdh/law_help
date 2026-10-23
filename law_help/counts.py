"""Counts that take minutes on a million rows (topics, judgments per section of an act), kept in
the `stored_counts` table so a server restart doesn't recount them.

They only change when the importer adds or relinks judgments, so `update`, `structure` and
`citations` call refresh() at the end, and `python -m law_help.importer counts` does it alone."""

import logging

import psycopg
from psycopg.types.json import Jsonb

log = logging.getLogger(__name__)


def cached(conn: psycopg.Connection, key: str, fn):
    """The stored value for key, or fn() computed now and stored."""
    row = conn.execute("SELECT value FROM stored_counts WHERE key = %s", (key,)).fetchone()
    if row:
        return row["value"]
    value = fn()
    store(conn, key, value)
    return value


def store(conn: psycopg.Connection, key: str, value) -> None:
    conn.execute("INSERT INTO stored_counts (key, value) VALUES (%s, %s) "
                 "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, computed_at = now()",
                 (key, Jsonb(value)))
    conn.commit()


def clear(conn: psycopg.Connection) -> None:
    conn.execute("DELETE FROM stored_counts")
    conn.commit()


def refresh(conn: psycopg.Connection) -> int:
    """Recount everything the pages ask for first: each topic, and each act's section counts for
    both courts together. Per-court section counts are recounted when next asked for."""
    from . import bareacts, topics
    from .acts_api import compute_section_counts
    from .topics_api import count_topic, topic_counts

    clear(conn)
    n = 0
    for t in topics.TOPICS:
        store(conn, f"topic:count:{t['slug']}", count_topic(conn, t))
        store(conn, f"topic:detail:{t['slug']}", topic_counts(conn, t))
        n += 2
        log.info("counted topic %s", t["slug"])
    for slug in bareacts.acts():
        store(conn, f"sections:{slug}:", compute_section_counts(conn, slug, None))
        n += 1
        log.info("counted sections of %s", slug)
    return n
