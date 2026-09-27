"""Similar judgments (law_help.similar) against the test database. Skipped when unreachable."""

import os

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from law_help import db, similar
from law_help.importer import UPSERT_SQL

TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://law:law@localhost:5432/law_help_test")

IPC = "Indian Penal Code, 1860"
CRPC = "Code of Criminal Procedure, 1973"


def _row(link, case, decided, title="A Vs STATE"):
    case_type, number, year = case.split("/")
    return {
        "source": "test", "court": "Rajasthan High Court", "bench": "jaipur", "cnr": None,
        "pdf_link": link, "pdf_key": f"data/pdf/{link}", "case_type": case_type,
        "case_number": int(number), "case_year": int(year), "title": f"{case} of {title}",
        "petitioner": None, "respondent": None, "judges": [], "bench_strength": None,
        "disposal_nature": None, "date_of_registration": None, "decision_date": decided,
        "description": None,
    }


# link: (acts_cited, cases_cited citation strings, neutral_citation)
FIELDS = {
    "this": ([{"act": IPC, "sections": ["302", "34"]}, {"act": CRPC, "sections": ["439"]}],
             ["(2012) 10 SCC 303"], "2024:RJ-JP:1"),
    "same_case": ([{"act": IPC, "sections": ["302", "34"]}], [], None),     # an earlier order in this case
    "twin": ([{"act": IPC, "sections": ["302"]}], [], "2024:RJ-JP:2"),       # both cite a and b
    "twin_connected": ([{"act": IPC, "sections": ["302"]}], [], "2024:RJ-JP:2"),
    "sections": ([{"act": IPC, "sections": ["302", "34"]}], [], None),      # IPC 302 and 34 only
    "bail": ([{"act": CRPC, "sections": ["439"]}], [], None),               # CrPC 439 only, other type
    "reported": ([], ["(2012) 10 SCC 303"], None),                          # same SCC citation
    "unrelated": ([{"act": "Motor Vehicles Act, 1988", "sections": ["166"]}], [], None),
    "a": ([], [], None), "b": ([], [], None), "d": ([], [], None),
    "later": ([], [], None),                                                # cites this and d
}

ROWS = [
    _row("this", "CRLMB/10/2024", "2024-05-01"),
    _row("same_case", "CRLMB/10/2024", "2024-03-01"),
    _row("twin", "CRLMB/20/2024", "2024-02-01", "TWIN Vs STATE"),
    _row("twin_connected", "CRLMB/21/2024", "2024-02-01", "TWIN2 Vs STATE"),
    _row("sections", "CRLMB/30/2023", "2023-01-01"),
    _row("bail", "CW/40/2023", "2023-01-01"),
    _row("reported", "CRLA/50/2020", "2020-01-01"),
    _row("unrelated", "CRLMB/60/2024", "2024-01-01"),
    _row("a", "CW/1/2010", "2010-01-01"),
    _row("b", "CW/2/2011", "2011-01-01"),
    _row("d", "CRLMB/3/2015", "2015-01-01", "DEVA Vs STATE"),
    _row("later", "CRLMB/4/2025", "2025-01-01"),
]

CITES = [("this", "a"), ("this", "b"), ("twin", "a"), ("twin", "b"), ("twin_connected", "a"),
         ("twin_connected", "b"), ("later", "this"), ("later", "d")]


@pytest.fixture(scope="module")
def ids():
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
        for link, (acts, cites, nc) in FIELDS.items():
            cur.execute("UPDATE judgments SET acts_cited = %s, cases_cited = %s, neutral_citation = %s "
                        "WHERE pdf_link = %s",
                        (Jsonb(acts), Jsonb([{"name": "X v. Y", "citations": [c]} for c in cites]), nc, link))
    ids = {r["pdf_link"]: r["id"] for r in conn.execute("SELECT id, pdf_link FROM judgments")}
    conn.cursor().executemany("INSERT INTO citations (citing_id, cited_id) VALUES (%s, %s)",
                              [(ids[a], ids[b]) for a, b in CITES])
    conn.execute("INSERT INTO cited_counts SELECT cited_id, count(*) FROM citations GROUP BY 1")
    conn.commit()
    yield ids
    mp.undo()
    conn.close()


@pytest.fixture(scope="module")
def client(ids):
    from law_help.api import app
    return TestClient(app)


def test_similar_ranks_shared_cases_first_and_says_why(client, ids):
    results = client.get(f"/judgments/{ids['this']}/similar").json()["results"]
    by_id = {r["id"]: r for r in results}
    assert results[0]["id"] in (ids["twin"], ids["twin_connected"])
    assert results[0]["reason"] == "2 cases in common · IPC 302"
    assert by_id[ids["sections"]]["reason"] == "IPC 302, 34"
    assert by_id[ids["reported"]]["reason"] == "1 case in common"
    assert by_id[ids["d"]]["reason"] == "cited together 1 time"
    assert by_id[ids["bail"]]["reason"] == "CrPC 439"
    # Two IPC sections and the case type beat one procedural section.
    order = [r["id"] for r in results]
    assert order.index(ids["sections"]) < order.index(ids["bail"])
    assert {"title", "case_type", "decision_date", "court"} <= results[0].keys()


def test_similar_skips_own_case_connected_copies_and_unrelated(client, ids):
    got = {r["id"] for r in client.get(f"/judgments/{ids['this']}/similar").json()["results"]}
    assert ids["this"] not in got and ids["same_case"] not in got and ids["unrelated"] not in got
    assert len(got & {ids["twin"], ids["twin_connected"]}) == 1   # one common order, shown once


def test_similar_limit_and_404(client, ids):
    assert len(client.get(f"/judgments/{ids['this']}/similar", params={"limit": 2}).json()["results"]) == 2
    assert client.get(f"/judgments/{ids['this']}/similar", params={"limit": 11}).status_code == 422
    assert client.get("/judgments/999999999/similar").status_code == 404


def test_judgment_with_nothing_in_common_has_no_similar(client, ids):
    assert client.get(f"/judgments/{ids['unrelated']}/similar").json()["results"] == []


def test_page_loads_the_panel(client):
    assert "/static/similar.js" in client.get("/judgment").text
    assert "similarPanel" in client.get("/static/similar.js").text


def test_reason_line():
    assert similar.reason(3, 0, [(IPC, "302")]) == "3 cases in common · IPC 302"
    assert similar.reason(1, 2, [(IPC, "302")]) == "1 case in common · cited together 2 times"
    assert similar.reason(0, 0, [(IPC, "302"), (IPC, "34"), (CRPC, "439")]) == "IPC 302, 34"
    assert similar.reason(0, 0, [("Constitution of India", "21")]) == "Constitution art. 21"
    assert similar.reason(0, 0, [(IPC, s) for s in "1234"]) == "IPC 1, 2, 3…"
