"""Links to Supreme Court judgments and the landmark tag.

The database test uses TEST_DATABASE_URL and skips when it is unreachable, like test_api.py.
"""

import os
from datetime import date

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from law_help import db, importer, landmark
from law_help.importer import SC_UPSERT_SQL, UPSERT_SQL

TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://law:law@localhost:5432/law_help_test")


def sc(id, pet, res, year, nc=None, scr=None):
    return {"id": id, "petitioner": pet, "respondent": res, "decision_date": date(year, 5, 1),
            "neutral_citation": nc, "report_citation": scr}


INDEX = landmark.SupremeIndex([
    sc(1, "GIAN SINGH", "STATE OF PUNJAB AND ANR.", 2012, "2012 INSC 700", "[2012] 8 S.C.R. 753"),
    sc(2, "ARNESH KUMAR", "STATE OF BIHAR & ANR.", 2014),
    sc(3, "RAM SINGH", "STATE OF HARYANA", 2001),
    sc(4, "RAM SINGH", "STATE OF HARYANA", 2001),   # two judgments, same parties, same year
])


def test_citation_keys():
    assert landmark.insc_key("2024 INSC 0735") == "2024 INSC 735"
    assert landmark.scr_key("(2012) 8 SCR 753") == landmark.scr_key("[2012] 8 S.C.R. 753") == "2012/8/753"
    assert landmark.scr_key("2012 (8) SCR 753") == "2012/8/753"
    assert landmark.scr_key("(2012) 8 SCC 753") is None


@pytest.mark.parametrize("cited, expected", [
    ({"name": "Someone v. Other", "citations": ["2012 INSC 700"]}, 1),        # by neutral citation
    ({"name": None, "citations": ["(2012) 8 SCR 753"]}, 1),                  # by S.C.R.
    ({"name": "Gian Singh v. State of Punjab & Anr.", "citations": ["(2012) 10 SCC 303"]}, 1),
    ({"name": "Arnesh Kumar Vs. State of Bihar", "citations": ["(2015) 8 SCC 273"]}, 2),  # reported a year on
    ({"name": "Arnesh Kumar v. State of Bihar", "citations": ["(2019) 8 SCC 273"]}, None),  # wrong year
    ({"name": "Arnesh Kumar v. State of Bihar", "citations": ["2014 RLW 1 (Raj)"]}, None),  # not an SC reporter
    ({"name": "Arnesh Kumar v. State of Bihar", "citations": []}, None),
    ({"name": "Ram Singh v. State of Haryana", "citations": ["AIR 2001 SC 55"]}, None),     # ambiguous
])
def test_find(cited, expected):
    assert INDEX.find(cited) == expected


@pytest.fixture
def conn(monkeypatch):
    try:
        c = db.connect(TEST_URL)
    except Exception:
        pytest.skip("no test database available")
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    db.init_schema(c)
    c.execute("TRUNCATE judgments, citations, cited_counts")
    c.commit()
    yield c
    c.close()


def hc_row(i, cited):
    return {
        "source": "aws-hc-judgments", "court": "Rajasthan High Court", "bench": "jaipur", "cnr": f"RJ{i}",
        "pdf_link": f"court/{i}.pdf", "pdf_key": f"data/pdf/{i}.pdf", "case_type": "CRLMB",
        "case_number": i, "case_year": 2024, "title": f"CRLMB/{i}/2024 of A{i} Vs STATE", "petitioner": f"A{i}",
        "respondent": "STATE", "judges": [], "bench_strength": "single", "disposal_nature": None,
        "date_of_registration": None, "decision_date": "2024-06-01", "description": None, "_cited": cited,
    }


def sc_row(cnr, pet, year):
    return {
        "source": "aws-sc-judgments", "court": "Supreme Court of India", "bench": "supreme court",
        "cnr": cnr, "pdf_link": f"sc/{cnr}.pdf", "pdf_key": f"sc/{cnr}.pdf", "case_type": "CRIMINAL APPEAL",
        "case_number": 1, "case_year": year, "title": f"{pet} Vs STATE OF BIHAR", "petitioner": pet,
        "respondent": "STATE OF BIHAR & ANR.", "judges": [], "bench_strength": "2-judge",
        "disposal_nature": None, "date_of_registration": None, "decision_date": f"{year}-07-02",
        "description": None, "neutral_citation": None, "report_citation": None}


def test_landmark_counts_links_and_filter(conn):
    with conn.cursor() as cur:
        cur.execute(SC_UPSERT_SQL, sc_row("ESCR1", "ARNESH KUMAR", 2014))
        cur.execute(SC_UPSERT_SQL, sc_row("ESCR2", "LALITA KUMARI", 2013))
        # 26 bail orders cite Arnesh Kumar, two of them one common order (same text): 25 distinct.
        # Three cite Lalita Kumari (two distinct).
        for i in range(26):
            cited = [{"name": "Arnesh Kumar v. State of Bihar", "citations": ["(2014) 8 SCC 273"]}]
            if i < 3:
                cited.append({"name": "Lalita Kumari v. State of Bihar", "citations": ["(2014) 2 SCC 1"]})
            row = hc_row(i, cited)
            cur.execute(UPSERT_SQL, row)
            cur.execute("UPDATE judgments SET cases_cited = %s, full_text = %s WHERE cnr = %s",
                        (Jsonb(cited), "common order" if i < 2 else f"order {i}", row["cnr"]))
    conn.commit()
    importer.link_citations(conn)
    ids = {r["cnr"]: r["id"] for r in conn.execute("SELECT id, cnr FROM judgments WHERE cnr LIKE 'ESCR%'")}
    counts = {r["judgment_id"]: r["n"] for r in conn.execute("SELECT judgment_id, n FROM cited_counts")}
    assert (counts[ids["ESCR1"]], counts[ids["ESCR2"]]) == (25, 2)

    from law_help.api import app

    client = TestClient(app)
    got = client.get("/judgments", params={"landmark": "true"}).json()
    assert [r["cnr"] for r in got["results"]] == ["ESCR1"]
    assert got["results"][0]["landmark"] is True and got["results"][0]["cited_by_count"] == 25
    detail = client.get(f"/judgments/{ids['ESCR1']}").json()
    assert detail["cited_by_total"] == 25 and detail["landmark"]
    assert client.get(f"/judgments/{ids['ESCR2']}").json()["landmark"] is False
    one = client.get("/judgments", params={"court": "rajasthan", "page_size": 1}).json()["results"][0]
    assert one["landmark"] is False and one["cited_by_count"] == 0
    assert client.get(f"/judgments/{one['id']}").json()["cites"][0]["id"] == ids["ESCR1"]

    # below LANDMARK_MIN citations nothing is a landmark, however it ranks
    conn.execute("UPDATE judgments SET cases_cited = '[]' WHERE cnr ~ '^RJ(1[0-9]|2[0-9])$'")
    conn.commit()
    importer.link_citations(conn)
    assert client.get("/judgments", params={"landmark": "true"}).json()["total"] == 0
