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


def test_words_side_by_side_rank_above_scattered_words(client):
    filler = "The parties were heard at length. " * 200
    rows = [
        # All four words, many times over, but never next to each other.
        dict(ROWS[1], cnr="RJHCSCATTER", pdf_link="test/scatter.pdf", decision_date="2025-01-01",
             text="The armed guard stood by. The parties were heard. Police force was used. Counsel argued. "
                  "The tribunal erred. Counsel argued. Contempt of court. " * 40),
        dict(ROWS[1], cnr="RJHCPHRASE", pdf_link="test/phrase.pdf", decision_date="2020-01-01",
             text=f"{filler} Contempt petition against non-compliance with the order of the Armed Forces "
                  f"Tribunal. {filler}"),
    ]
    conn = db.connect(TEST_URL)
    try:
        with conn.cursor() as cur:
            cur.executemany(UPSERT_SQL, rows)
            cur.executemany("UPDATE judgments SET full_text = %(text)s WHERE cnr = %(cnr)s", rows)
        conn.commit()
        body = client.get("/judgments", params={"q": "armed force tribunal contempt"}).json()
        assert [r["cnr"] for r in body["results"]] == ["RJHCPHRASE", "RJHCSCATTER"]
    finally:
        conn.execute("DELETE FROM judgments WHERE cnr IN ('RJHCSCATTER', 'RJHCPHRASE')")
        conn.commit()
        conn.close()


def test_every_searched_word_counts(client):
    rows = [
        # Newer and all about the Tribunal, but contempt comes up once.
        dict(ROWS[1], cnr="RJHCTRIBUNAL", pdf_link="test/tribunal.pdf", decision_date="2025-01-01",
             text="No contempt of the Armed Forces Tribunal was alleged. "
                  + "The Armed Forces Tribunal granted pension. " * 30),
        dict(ROWS[1], cnr="RJHCCONTEMPT", pdf_link="test/contempt.pdf", decision_date="2020-01-01",
             text="The order of the Armed Forces Tribunal was not obeyed. " * 3 + "Heard at length. " * 50
                  + "The contempt is wilful. " * 10),
    ]
    conn = db.connect(TEST_URL)
    try:
        with conn.cursor() as cur:
            cur.executemany(UPSERT_SQL, rows)
            cur.executemany("UPDATE judgments SET full_text = %(text)s WHERE cnr = %(cnr)s", rows)
        conn.commit()
        body = client.get("/judgments", params={"q": "armed force tribunal contempt"}).json()
        assert [r["cnr"] for r in body["results"]] == ["RJHCCONTEMPT", "RJHCTRIBUNAL"]
    finally:
        conn.execute("DELETE FROM judgments WHERE cnr IN ('RJHCTRIBUNAL', 'RJHCCONTEMPT')")
        conn.commit()
        conn.close()


def test_common_word_search_caps_count_and_ranks_recent_matches(client, monkeypatch):
    from law_help import api
    monkeypatch.setattr(api, "SEARCH_COUNT_CAP", 1)
    monkeypatch.setattr(api, "SEARCH_RANK_ALL", 1)
    monkeypatch.setattr(api, "SEARCH_RANK_POOL", 1)
    body = client.get("/judgments", params={"q": "appeal or pension"}).json()
    assert body["total"] == 1 and body["total_capped"] and body["ranked"] == 1
    assert [r["cnr"] for r in body["results"]] == ["RJHC020000011994"]  # decided 2024, the newer one

    body = client.get("/judgments", params={"q": "pension"}).json()
    assert body["total"] == 1 and not body["total_capped"] and body["ranked"] == 1


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


def test_citation_line(client):
    ladu = client.get("/judgments", params={"case_type": "CRLA"}).json()["results"][0]
    j = client.get(f"/judgments/{ladu['id']}").json()
    assert j["citation"] == "Ladu v. State (Raj.) [S.B. Criminal Appeal No. 28/1994, decided on 20.05.2024, Jaipur Bench]"


def test_citation_line_formats():
    from datetime import date

    from law_help.api import citation_line
    j = {"petitioner": "RAM", "respondent": "UNION OF INDIA", "parties": {"petitioners": ["RAM", "SHYAM"]},
         "neutral_citation": "2024:RJ-JD:12345-DB", "case_type": "CW", "case_number": 5, "case_year": 2020,
         "bench_strength": "division", "decision_date": date(2023, 3, 1), "bench": "jodhpur"}
    assert citation_line(j) == ("Ram & Ors. v. Union of India, 2024:RJ-JD:12345-DB (Raj.) "
                                "[D.B. Civil Writ Petition No. 5/2020, decided on 01.03.2023, Jodhpur Bench]")
    assert citation_line({"title": "CW/1/2020 of A Vs B"}) == "A v. B (Raj.)"


def test_section_search_also_finds_the_new_code(client):
    # The fixture cites IPC 120B, which BNS s. 61(2) replaced.
    body = client.get("/judgments", params={"act": "BNS", "section": "61"}).json()
    assert body["total"] == 1
    assert [(e["act"], e["ref"]) for e in body["equivalents"]] == [("ipc", "120A"), ("ipc", "120B")]
    assert client.get("/judgments", params={"act": "BNS", "section": "61", "equivalent": "false"}).json()["total"] == 0
    assert client.get("/judgments", params={"act": "IPC", "section": "120-B"}).json()["equivalents"][0]["ref"] == "61(2)"


def test_bare_act_pages(client):
    assert client.get("/acts").status_code == 200
    acts = {a["slug"]: a for a in client.get("/api/acts").json()}
    assert acts["ipc"]["replaced_by"] == "bns" and acts["bns"]["replaces"] == "ipc"
    contents = client.get("/api/acts/cpc").json()
    assert any(s["number"] == "Order VII Rule 11" for s in contents["sections"])
    sec = client.get("/api/acts/ipc/sections/120B").json()
    assert sec["title"] == "Punishment of criminal conspiracy"
    assert sec["equivalents"][0]["ref"] == "61(2)"
    assert sec["cited_by"]["total"] == 1
    assert client.get("/api/acts/ipc/sections/9999").status_code == 404
    assert client.get("/api/acts/nope").status_code == 404


def test_detail_carries_case_status_links(client):
    ladu = client.get("/judgments", params={"case_type": "CRLA"}).json()["results"][0]
    j = client.get(f"/judgments/{ladu['id']}").json()
    assert j["status_links"]["links"][0]["label"] == "Check case status"
    assert j["status_links"]["fields"][0] == {"label": "CNR", "value": "RJHC020000011994", "copy": True}
    assert client.get("/static/case-status.js").status_code == 200
