"""Section pages: judgments citing a section in the old or new code (TEST_DATABASE_URL)."""

import os

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from law_help import counts, db
from law_help.importer import UPSERT_SQL

IPC, BNS = "Indian Penal Code, 1860", "Bharatiya Nyaya Sanhita, 2023"


def row(n, court, bench, decided, acts):
    return {
        "source": "test", "court": court, "bench": bench, "cnr": f"SEC{n}", "pdf_link": f"test/s{n}.pdf",
        "pdf_key": f"data/pdf/test/s{n}.pdf", "case_type": "CRLMB", "case_number": n, "case_year": 2024,
        "title": f"CRLMB/{n}/2024 of A{n} Vs STATE", "petitioner": f"A{n}", "respondent": "STATE",
        "judges": ["X"], "bench_strength": "single", "disposal_nature": "ALLOWED",
        "date_of_registration": None, "decision_date": decided, "description": "Bail", "acts": acts,
    }


ROWS = [
    row(1, "Rajasthan High Court", "jaipur", "2020-01-01", [{"act": IPC, "sections": ["420", "406"]}]),
    row(2, "Rajasthan High Court", "jodhpur", "2025-03-01", [{"act": BNS, "sections": ["318(4)"]}]),
    row(3, "Supreme Court of India", "supreme court", "2018-05-01", [{"act": IPC, "sections": ["420"]}]),
    row(4, "Rajasthan High Court", "jaipur", "2024-01-01", [{"act": IPC, "sections": ["302"]}]),
]

TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://law:law@localhost:5432/law_help_test")


@pytest.fixture(scope="module")
def client():
    try:
        conn = db.connect(TEST_URL)
    except Exception:
        pytest.skip("no test database available")
    mp = pytest.MonkeyPatch()
    mp.setenv("DATABASE_URL", TEST_URL)
    db.init_schema(conn)
    conn.execute("TRUNCATE judgments, citations, cited_counts")
    with conn.cursor() as cur:
        cur.executemany(UPSERT_SQL, ROWS)
        for r in ROWS:
            cur.execute("UPDATE judgments SET acts_cited = %s WHERE cnr = %s", (Jsonb(r["acts"]), r["cnr"]))
        # The Supreme Court judgment is cited most, then the 2020 one.
        cur.execute("INSERT INTO cited_counts SELECT id, CASE cnr WHEN 'SEC3' THEN 5 ELSE 2 END "
                    "FROM judgments WHERE cnr IN ('SEC1', 'SEC3')")
    conn.commit()
    counts.clear(conn)
    from law_help.api import app
    yield TestClient(app)
    mp.undo()
    conn.close()


def test_old_and_new_code_together_most_cited_first(client):
    body = client.get("/api/acts/ipc/sections/420/judgments").json()
    assert body["title"].startswith("Cheating")
    assert [e["ref"] for e in body["equivalents"]] == ["318(4)"]
    assert [r["cnr"] for r in body["results"]] == ["SEC3", "SEC1", "SEC2"]
    assert body["results"][0]["cited_by_count"] == 5 and body["total"] == 3
    # The same from the new code's side.
    assert client.get("/api/acts/bns/sections/318/judgments").json()["total"] == 3


def test_court_filter_and_pages(client):
    body = client.get("/api/acts/ipc/sections/420/judgments", params={"court": "rajasthan"}).json()
    assert [r["cnr"] for r in body["results"]] == ["SEC1", "SEC2"]
    body = client.get("/api/acts/ipc/sections/420/judgments", params={"page": 2, "page_size": 2}).json()
    assert body["total"] == 3 and [r["cnr"] for r in body["results"]] == ["SEC2"]
    assert client.get("/api/acts/ipc/sections/420/judgments", params={"court": "x"}).status_code == 422
    assert client.get("/api/acts/ipc/sections/9999/judgments").status_code == 404


def test_counts_per_section(client):
    counts = client.get("/api/acts/ipc/judgment-counts").json()
    assert counts == {"420": 3, "406": 1, "302": 1}
    assert client.get("/api/acts/bns/judgment-counts").json()["318"] == 3
    assert client.get("/api/acts/ipc/judgment-counts", params={"court": "supreme"}).json() == {"420": 1}
    # Each count matches its section page.
    for number, n in counts.items():
        assert client.get(f"/api/acts/ipc/sections/{number}/judgments").json()["total"] == n


def test_section_page_is_served(client):
    res = client.get("/acts", params={"act": "ipc", "s": "420", "view": "judgments"})
    assert res.status_code == 200 and "acts.js" in res.text
    assert "function resultItem" in client.get("/static/common.js").text
