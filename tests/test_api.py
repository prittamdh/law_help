"""API tests against a real Postgres. Skipped when DATABASE_URL is unreachable."""

import pytest
from fastapi.testclient import TestClient

from law_help import db
from law_help.importer import UPSERT_SQL

ROWS = [
    {
        "source": "test", "court": "Rajasthan High Court", "bench": "jaipur",
        "cnr": "RJHC020000011994", "pdf_link": "test/a.pdf", "pdf_key": "data/pdf/test/a.pdf",
        "case_type": "CRLA", "case_number": 28, "case_year": 1994,
        "title": "CRLA/28/1994 of LADU Vs STATE", "petitioner": "LADU", "respondent": "STATE",
        "judges": ["GANESH RAM MEENA"], "bench_strength": "single",
        "disposal_nature": "ALLOWED", "date_of_registration": "1994-01-17",
        "decision_date": "2024-05-20", "description": "Criminal appeal against conviction under NDPS Act",
    },
    {
        "source": "test", "court": "Rajasthan High Court", "bench": "jodhpur",
        "cnr": "RJHC010000021990", "pdf_link": "test/b.pdf", "pdf_key": "data/pdf/test/b.pdf",
        "case_type": "CW", "case_number": 5, "case_year": 2020,
        "title": "CW/5/2020 of RAM Vs UNION OF INDIA", "petitioner": "RAM", "respondent": "UNION OF INDIA",
        "judges": ["PANKAJ BHANDARI", "VINOD KUMAR BHARWANI"], "bench_strength": "division",
        "disposal_nature": "DISMISSED", "date_of_registration": "2020-01-02",
        "decision_date": "2023-03-01", "description": "Writ petition about pension arrears",
    },
]


@pytest.fixture(scope="module")
def client():
    try:
        conn = db.connect()
    except Exception:
        pytest.skip("no database available")
    db.init_schema(conn)
    conn.execute("DELETE FROM judgments WHERE source = 'test'")
    with conn.cursor() as cur:
        cur.executemany(UPSERT_SQL, ROWS)
    conn.commit()
    from law_help.api import app
    yield TestClient(app)
    conn.execute("DELETE FROM judgments WHERE source = 'test'")
    conn.commit()
    conn.close()


def test_full_text_search(client):
    body = client.get("/judgments", params={"q": "pension"}).json()
    assert [r["cnr"] for r in body["results"]] == ["RJHC010000021990"]
    assert body["results"][0]["pdf_url"].endswith("data/pdf/test/b.pdf")


def test_filter_by_judge_on_division_bench(client):
    body = client.get("/judgments", params={"judge": "vinod kumar bharwani"}).json()
    assert body["total"] == 1 and body["results"][0]["bench"] == "jodhpur"


def test_filter_by_date_and_disposal(client):
    body = client.get("/judgments", params={"decided_from": "2024-01-01", "disposal": "allowed"}).json()
    assert [r["case_type"] for r in body["results"]] == ["CRLA"]


def test_get_one_and_404(client):
    first = client.get("/judgments", params={"q": "NDPS"}).json()["results"][0]
    detail = client.get(f"/judgments/{first['id']}").json()
    assert detail["description"].startswith("Criminal appeal")
    assert client.get("/judgments/999999999").status_code == 404
