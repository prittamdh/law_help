"""Topics (law_help.topics): the rules as SQL, the topic filter on /judgments, and /api/topics."""

import os

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from law_help import db, topics
from law_help.importer import UPSERT_SQL


def test_every_topic_has_rules_and_a_unique_slug():
    assert len({t["slug"] for t in topics.TOPICS}) == len(topics.TOPICS)
    for t in topics.TOPICS:
        sql, params = topics.topic_sql(t)
        assert sql.startswith("(") and params, t["slug"]
        assert topics.describe(t)


def test_keyword_query_is_a_phrase_in_title_or_opening_lines():
    assert topics.keyword_query(["dishonour of cheque", "NDPS"]) == \
        "(dishonour:AB <-> of:AB <-> cheque:AB) | (ndps:AB)"
    # Punctuation can't break the tsquery syntax.
    assert topics.keyword_query(["Art. 226!", "&|"]) == "(art:AB <-> 226:AB)"
    assert topics.keyword_query([]) is None


def test_cited_sections_include_sub_sections_and_whole_acts():
    frags = topics.cited_fragments(topics.get("murder"))
    assert {"act": "Indian Penal Code, 1860", "sections": ["302"]} in frags
    assert {"act": "Bharatiya Nyaya Sanhita, 2023", "sections": ["103(1)"]} in frags
    assert topics.cited_fragments(topics.get("ndps")) == [{"act": "Narcotic Drugs and Psychotropic Substances Act, 1985"}]


def test_topic_sql_combines_rules_with_or():
    sql, params = topics.topic_sql(topics.get("writs"), prefix="t")
    assert "acts_cited @> %(t_a0)s" in sql and " OR case_type = ANY(%(t_ct)s)" in sql
    assert "case_type LIKE ANY(%(t_ctp)s)" in sql and "search @@ to_tsquery('english', %(t_kw)s)" in sql
    assert params["t_ct"] == ["CW", "CRLW", "HC"] and params["t_ctp"] == ["WRIT PETITION%"]
    assert topics.describe(topics.get("cheque-bounce"))[0] == "NI Act s. 138, 139, 141, 142, 143A, 147, 148"


def _row(n, case_type, description, bench="jaipur", court="Rajasthan High Court", date="2024-01-01"):
    return {"source": "test", "court": court, "bench": bench, "cnr": f"TOPIC{n}", "pdf_link": f"topic/{n}.pdf",
            "pdf_key": f"data/pdf/topic/{n}.pdf", "case_type": case_type, "case_number": n, "case_year": 2024,
            "title": f"{case_type}/{n}/2024 of A Vs STATE", "petitioner": "A", "respondent": "STATE",
            "judges": ["X"], "bench_strength": "single", "disposal_nature": "DISPOSED",
            "date_of_registration": None, "decision_date": date, "description": description}


ROWS = [
    _row(1, "CRLMB", "Second bail application under Section 439 CrPC", date="2024-03-01"),
    _row(2, "CRLR", "Revision against conviction", date="2023-05-01"),
    _row(3, "WRIT PETITION (CIVIL)", "Fundamental rights of prisoners", bench="supreme court",
         court="Supreme Court of India", date="2022-01-01"),
    _row(4, "CRLA", "Appeal against conviction", bench="jodhpur", date="2025-02-01"),
    _row(5, "CW", "Writ petition about pension arrears", date="2021-01-01"),
]
ACTS = {
    "TOPIC1": [{"act": "Code of Criminal Procedure, 1973", "sections": ["439"]},
               {"act": "Narcotic Drugs and Psychotropic Substances Act, 1985", "sections": ["8", "21"]}],
    "TOPIC2": [{"act": "Negotiable Instruments Act, 1881", "sections": ["138"]}],
    "TOPIC4": [{"act": "Bharatiya Nyaya Sanhita, 2023", "sections": ["103(1)"]}],
}

TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://law:law@localhost:5432/law_help_test")


@pytest.fixture(scope="module")
def conn():
    try:
        conn = db.connect(TEST_URL)
    except Exception:
        pytest.skip("no test database available")
    db.init_schema(conn)
    conn.execute("TRUNCATE judgments, cited_counts, landmark_thresholds, treatments")
    with conn.cursor() as cur:
        cur.executemany(UPSERT_SQL, ROWS)
        for cnr, acts in ACTS.items():
            cur.execute("UPDATE judgments SET acts_cited = %s WHERE cnr = %s", (Jsonb(acts), cnr))
        # Bail is in the full text of the murder appeal, but not its opening lines.
        cur.execute("UPDATE judgments SET full_text = 'The appellant was on bail during trial.' WHERE cnr = 'TOPIC4'")
        cur.execute("INSERT INTO cited_counts SELECT id, CASE cnr WHEN 'TOPIC1' THEN 3 ELSE 1 END "
                    "FROM judgments WHERE cnr IN ('TOPIC1', 'TOPIC4')")
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def client(conn):
    mp = pytest.MonkeyPatch()
    mp.setenv("DATABASE_URL", TEST_URL)
    from law_help import topics_api
    from law_help.api import app
    topics_api.clear_cache()
    yield TestClient(app)
    topics_api.clear_cache()
    mp.undo()


def _matching(conn, slug):
    where, params = topics.topic_sql(topics.get(slug))
    return sorted(r["cnr"] for r in conn.execute(f"SELECT cnr FROM judgments WHERE {where}", params))


@pytest.mark.parametrize("slug, cnrs", [
    ("bail", ["TOPIC1"]),                 # case type, CrPC 439, and "bail" in the opening lines
    ("ndps", ["TOPIC1"]),                 # any section of the act
    ("cheque-bounce", ["TOPIC2"]),
    ("murder", ["TOPIC4"]),               # BNS 103(1) is a sub-section of 103
    ("writs", ["TOPIC3", "TOPIC5"]),      # CW, and a Supreme Court writ petition by its case type
    ("service", ["TOPIC5"]),              # "pension" in the opening lines
    ("arbitration", []),
])
def test_topic_rules_in_sql(conn, slug, cnrs):
    assert _matching(conn, slug) == cnrs


def test_judgments_filter_by_topic(client):
    body = client.get("/judgments", params={"topic": "writs"}).json()
    assert body["total"] == 2
    assert [r["cnr"] for r in body["results"]] == ["TOPIC3", "TOPIC5"]  # newest first
    assert client.get("/judgments", params={"topic": "writs", "q": "pension"}).json()["total"] == 1
    assert client.get("/judgments", params={"topic": "writs", "court": "supreme"}).json()["results"][0]["cnr"] == "TOPIC3"
    assert client.get("/judgments", params={"topic": "nope"}).status_code == 422


def test_topics_list_and_page(client):
    listed = {t["slug"]: t for t in client.get("/api/topics").json()}
    assert listed["writs"]["total"] == 2 and listed["arbitration"]["total"] == 0
    assert client.get("/api/topics", params={"counts": "false"}).json()[0]["total"] is None

    bail = client.get("/api/topics/bail").json()
    assert bail["name"] == "Bail" and bail["total"] == 1
    assert bail["by_bench"] == [{"bench": "jaipur", "n": 1}] and bail["by_year"] == [{"year": 2024, "n": 1}]
    assert [j["cnr"] for j in bail["most_cited"]] == ["TOPIC1"] and bail["most_cited"][0]["cited_by_count"] == 3
    assert bail["latest"][0]["headline"].startswith("Bail application")
    assert any(r.startswith("CrPC s. 436") for r in bail["rules"])

    writs = client.get("/api/topics/writs").json()
    assert writs["most_cited"] == [] and [j["cnr"] for j in writs["latest"]] == ["TOPIC3", "TOPIC5"]
    assert client.get("/api/topics/nope").status_code == 404
    res = client.get("/topics")
    assert res.status_code == 200 and "topics.js" in res.text
    assert 'href="/topics"' in client.get("/").text
