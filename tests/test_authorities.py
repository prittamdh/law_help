"""GET /judgments/citations, used by the saved page's list of authorities.

Uses TEST_DATABASE_URL and skips when it is unreachable, like test_api.py.
"""

import os

import pytest
from fastapi.testclient import TestClient

from law_help import db
from law_help.importer import SC_UPSERT_SQL, UPSERT_SQL

TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://law:law@localhost:5432/law_help_test")

HC = {
    "source": "test", "court": "Rajasthan High Court", "bench": "jaipur", "cnr": "RJAUTH1",
    "pdf_link": "test/auth1.pdf", "pdf_key": "data/pdf/test/auth1.pdf", "case_type": "CRLA",
    "case_number": 28, "case_year": 1994, "title": "CRLA/28/1994 of LADU Vs STATE", "petitioner": "LADU",
    "respondent": "STATE", "judges": [], "bench_strength": "single", "disposal_nature": "ALLOWED",
    "date_of_registration": None, "decision_date": "2024-05-20", "description": None,
}
SC = {
    "source": "test", "court": "Supreme Court of India", "bench": "supreme court", "cnr": "SCAUTH1",
    "pdf_link": "test/sc1.pdf", "pdf_key": "sc/sc1.pdf", "case_type": "CRIMINAL APPEAL",
    "case_number": 1031, "case_year": 2015, "title": "VIJAY SINGH Vs THE STATE OF BIHAR",
    "petitioner": "VIJAY SINGH", "respondent": "THE STATE OF BIHAR", "judges": [],
    "bench_strength": "2-judge", "disposal_nature": None, "date_of_registration": None,
    "decision_date": "2024-09-25", "description": None,
    "neutral_citation": "2024 INSC 735", "report_citation": "[2024] 10 S.C.R. 108",
}


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
        cur.execute(UPSERT_SQL, HC)
        cur.execute(SC_UPSERT_SQL, SC)
    conn.commit()
    ids = {r["cnr"]: r["id"] for r in conn.execute("SELECT id, cnr FROM judgments")}
    from law_help.api import app
    yield TestClient(app), ids
    mp.undo()
    conn.close()


def test_citations_in_the_order_asked(client):
    client, ids = client
    got = client.get("/judgments/citations", params={"ids": f"{ids['RJAUTH1']},999999999,{ids['SCAUTH1']}"}).json()
    assert [g["id"] for g in got] == [ids["RJAUTH1"], ids["SCAUTH1"]]   # unknown ids are left out
    hc, sc = got
    assert hc["citation"] == client.get(f"/judgments/{ids['RJAUTH1']}").json()["citation"]
    assert hc["case_name"] == "Ladu v. State" and hc["decision_date"] == "2024-05-20"
    assert sc["court"] == "Supreme Court of India"
    assert sc["case_name"] == "Vijay Singh v. The State of Bihar"
    assert sc["citation"] == ("Vijay Singh v. The State of Bihar, 2024 INSC 735 : [2024] 10 S.C.R. 108 "
                              "[Criminal Appeal No. 1031 of 2015, decided on 25.09.2024]")


def test_citations_rejects_bad_ids(client):
    client, _ = client
    assert client.get("/judgments/citations", params={"ids": "1,abc"}).status_code == 422
    assert client.get("/judgments/citations", params={"ids": ",".join(map(str, range(501)))}).status_code == 422
    assert client.get("/judgments/citations", params={"ids": ""}).json() == []
