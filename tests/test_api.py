"""API tests against a dedicated Postgres database (TEST_DATABASE_URL). Skipped when unreachable."""

import os

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

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
    conn.execute("TRUNCATE judgments")
    with conn.cursor() as cur:
        cur.executemany(UPSERT_SQL, ROWS)
        cur.execute(
            "UPDATE judgments SET bench_judges = %s, acts_cited = %s, summary = %s WHERE cnr = %s",
            (["GANESH RAM MEENA", "ANOOP KUMAR DHAND"],
             Jsonb([{"act": "Narcotic Drugs and Psychotropic Substances Act, 1985", "sections": ["8", "15"]},
                    {"act": "Indian Penal Code, 1860", "sections": ["120B"]}]),
             "The appellant challenged his conviction. The appeal is allowed.", "RJHC020000011994"),
        )
    conn.commit()
    from law_help.api import app
    yield TestClient(app)
    mp.undo()
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


def test_ui_pages_are_served(client):
    for path in ("/", "/judgment", "/saved"):
        res = client.get(path)
        assert res.status_code == 200 and "<html" in res.text
    assert client.get("/static/search.js").status_code == 200


def test_filter_by_act_alias_and_section(client):
    body = client.get("/judgments", params={"act": "NDPS Act"}).json()
    assert [r["cnr"] for r in body["results"]] == ["RJHC020000011994"]
    assert body["results"][0]["summary"].startswith("The appellant")
    assert client.get("/judgments", params={"act": "IPC", "section": "120-b"}).json()["total"] == 1
    assert client.get("/judgments", params={"act": "IPC", "section": "302"}).json()["total"] == 0
    assert client.get("/judgments", params={"section": "302"}).status_code == 422


def test_judge_filter_matches_judges_printed_on_the_pdf(client):
    body = client.get("/judgments", params={"judge": "Anoop Kumar Dhand"}).json()
    assert [r["cnr"] for r in body["results"]] == ["RJHC020000011994"]


def test_detail_and_stats_carry_structured_fields(client):
    first = client.get("/judgments", params={"q": "NDPS"}).json()["results"][0]
    detail = client.get(f"/judgments/{first['id']}").json()
    assert detail["acts_cited"][0]["sections"] == ["8", "15"]
    acts = {r["act"] for r in client.get("/stats").json()["top_acts"]}
    assert "Indian Penal Code, 1860" in acts


def test_results_carry_a_headline(client):
    body = client.get("/judgments", params={"act": "NDPS Act"}).json()
    assert body["results"][0]["headline"] == "Criminal appeal · NDPS Act s. 8, 15 · IPC s. 120B · Allowed"
    assert client.get(f"/judgments/{body['results'][0]['id']}").json()["headline"].startswith("Criminal appeal")
