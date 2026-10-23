"""RSS feeds of a search (GET /feed), against the test database (TEST_DATABASE_URL). Skipped when unreachable."""

import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from law_help import db
from law_help.feed import feed_title
from law_help.importer import UPSERT_SQL

TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://law:law@localhost:5432/law_help_test")


def _row(n: int, bench: str, decided: str, description: str, **kw) -> dict:
    return {
        "source": "test", "court": "Rajasthan High Court", "bench": bench, "cnr": f"RJHCFEED{n:04d}",
        "pdf_link": f"test/feed{n}.pdf", "pdf_key": f"data/pdf/test/feed{n}.pdf",
        "case_type": "CRLMB", "case_number": n, "case_year": 2024,
        "title": f"CRLMB/{n}/2024 of PETITIONER {n} Vs STATE OF RAJASTHAN", "petitioner": f"PETITIONER {n}",
        "respondent": "STATE OF RAJASTHAN", "judges": ["SAMEER JAIN"], "bench_strength": "single",
        "disposal_nature": "ALLOWED", "date_of_registration": None, "decision_date": decided,
        "description": description, **kw,
    }


# An old judgment decided most recently, and a newer arrival decided years ago: the feed
# orders by when law_help added them, not by decision date.
OLD = _row(1, "jaipur", "2024-06-01", "Bail application under NDPS Act")
NEW = _row(2, "jodhpur", "2019-02-11", "Bail application in a theft case")
OTHER = _row(3, "jaipur", "2024-05-01", "Writ petition about pension arrears")


@pytest.fixture(scope="module")
def client():
    try:
        conn = db.connect(TEST_URL)
    except Exception:
        pytest.skip("no test database available")
    mp = pytest.MonkeyPatch()
    mp.setenv("DATABASE_URL", TEST_URL)
    db.init_schema(conn)
    conn.execute("TRUNCATE judgments")
    with conn.cursor() as cur:
        cur.executemany(UPSERT_SQL, [OLD, NEW, OTHER])
        cur.execute("UPDATE judgments SET added_at = '2024-06-02' WHERE cnr IN ('RJHCFEED0001', 'RJHCFEED0003')")
        cur.execute("UPDATE judgments SET added_at = '2026-09-26 06:00+05:30', summary = %s, acts_cited = %s "
                    "WHERE cnr = 'RJHCFEED0002'",
                    ("The petitioner sought bail. Bail is granted.",
                     Jsonb([{"act": "Indian Penal Code, 1860", "sections": ["379"]}])))
    conn.commit()
    from law_help.api import app
    yield TestClient(app)
    mp.undo()
    conn.close()


def _items(resp):
    channel = ET.fromstring(resp.content).find("channel")
    return channel, channel.findall("item")


def test_feed_orders_by_when_added(client):
    resp = client.get("/feed", params={"q": "bail"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/rss+xml")
    channel, items = _items(resp)
    assert channel.findtext("title") == 'law_help: "bail"'
    assert channel.findtext("link") == "http://testserver/?q=bail"
    assert [i.findtext("title").split(":")[0] for i in items] == ["Petitioner 2 v. State of Rajasthan",
                                                                  "Petitioner 1 v. State of Rajasthan"]


def test_feed_item_fields(client):
    _, items = _items(client.get("/feed", params={"bench": "jodhpur"}))
    [item] = items
    guid = item.findtext("guid")
    assert item.find("guid").get("isPermaLink") == "false" and guid.isdigit()
    assert item.findtext("link") == f"http://testserver/judgment?id={guid}"
    assert item.findtext("title") == "Petitioner 2 v. State of Rajasthan: Bail application · IPC s. 379 · Allowed"
    description = item.findtext("description")
    assert description.startswith("Petitioner 2 v. State of Rajasthan (Raj.) [S.B. ")
    assert description.endswith("\n\nThe petitioner sought bail. Bail is granted.")
    assert parsedate_to_datetime(item.findtext("pubDate")) == datetime(2026, 9, 26, 0, 30, tzinfo=timezone.utc)


def test_feed_takes_the_same_filters_as_search(client):
    params = {"judge": "sameer jain", "decided_from": "2024-01-01", "bench": "jaipur"}
    search = client.get("/judgments", params=params).json()
    _, items = _items(client.get("/feed", params=params))
    assert sorted(int(i.findtext("guid")) for i in items) == sorted(r["id"] for r in search["results"])
    assert len(items) == 2
    # and rejects what search rejects
    assert client.get("/feed", params={"section": "302"}).status_code == 422
    assert client.get("/feed", params={"court": "nowhere"}).status_code == 422


def test_new_judgments_get_added_at_and_reimport_keeps_it(client):
    conn = db.connect(TEST_URL)
    try:
        before = conn.execute("SELECT added_at FROM judgments WHERE cnr = 'RJHCFEED0001'").fetchone()["added_at"]
        conn.execute(UPSERT_SQL, OLD)   # a daily update re-importing the partition
        conn.execute(UPSERT_SQL, _row(4, "jaipur", "2020-01-01", "Bail application, fresh"))
        conn.commit()
        assert conn.execute("SELECT added_at FROM judgments WHERE cnr = 'RJHCFEED0001'").fetchone()["added_at"] == before
        _, items = _items(client.get("/feed", params={"q": "fresh"}))
        assert [i.findtext("title").split(":")[0] for i in items] == ["Petitioner 4 v. State of Rajasthan"]
        _, items = _items(client.get("/feed"))
        assert items[0].findtext("title").startswith("Petitioner 4 ")
    finally:
        conn.execute("DELETE FROM judgments WHERE cnr = 'RJHCFEED0004'")
        conn.commit()
        conn.close()


def test_schema_backfills_added_at_from_decision_date(client):
    conn = db.connect(TEST_URL)
    try:
        conn.execute("ALTER TABLE judgments DROP COLUMN added_at")
        conn.commit()
        db.init_schema(conn)   # adds and backfills it once
        db.init_schema(conn)   # and is a no-op after
        rows = conn.execute("SELECT added_at::date = decision_date AS same FROM judgments").fetchall()
        assert rows and all(r["same"] for r in rows)
    finally:
        conn.execute("UPDATE judgments SET added_at = '2024-06-02' WHERE cnr IN ('RJHCFEED0001', 'RJHCFEED0003')")
        conn.execute("UPDATE judgments SET added_at = '2026-09-26 06:00+05:30' WHERE cnr = 'RJHCFEED0002'")
        conn.commit()
        conn.close()


def test_search_page_links_the_feed(client):
    html = client.get("/").text
    assert 'rel="alternate" type="application/rss+xml"' in html and "Follow (RSS)" in html


def test_feed_title():
    assert feed_title({}) == "law_help: new judgments"
    assert feed_title({"q": "bail", "act": "NDPS", "section": "37", "landmark": "true"}) == \
        'law_help: "bail" · act NDPS · s. 37 · landmarks'
