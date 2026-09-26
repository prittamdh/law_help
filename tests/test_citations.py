""""Cited by": finding this court's cases in a judgment's text and linking them to judgments."""

import os

import pytest

from law_help import db, extract
from law_help.importer import UPSERT_SQL, link_citations

BODY = """1. The issue stands finally adjudicated by a Co-ordinate Bench of this Court in
Sharvan Choudhary Vs. State of Rajasthan and Ors. : S.B. Civil Writ Petition No.4298/2025,
decided on 08.05.2025, which was followed in S.B. Civil Writ No.10564/2023 (titled as
Radha Vs. State of Rajasthan & Anr.).
2. Bail was earlier rejected vide order dated 01.02.2024 in S.B. Criminal Misc. Bail
Application No. 812/2024.
3. The Principal Seat at Jodhpur decided D.B. Special Appeal (Writ) No.61/2024 on 05.04.2024.
4. Learned counsel appearing in S.B. Civil Writ Petition No.6191/2025 submitted otherwise.
5. Let this matter be listed along with D.B. Civil Writ Petition No.1285/2025, Kalu Vs. State.
6. See also Ram v. State [2024:RJ-JP:2823-DB].
"""


def test_case_kind():
    assert extract.case_kind("Civil Writ Petition", "S") == "CW"
    assert extract.case_kind("Spl. Appl. Writ", "D") == "SAW"
    assert extract.case_kind("Criminal Misc. Bail Application", "S") == "CRLMB"
    assert extract.case_kind("Criminal Miscellaneous (Petition)", "S") == "CRLMP"
    assert extract.case_kind("Criminal Appeal", "D") == "CRLAD|CRLA"
    assert extract.case_kind("Civil Reference", "D") is None


def test_case_refs_keep_citations_and_skip_listings():
    body = extract.body(BODY) or BODY
    refs = extract.case_refs(body, {"CW/9999/2025"}, extract.cases_cited(body))
    assert refs == [
        "CW/4298/2025/sharvan choudhary/",
        "CW/10564/2023/radha/",
        "CRLMB/812/2024//",
        "SAW/61/2024//jodhpur",
        "NC:2024:RJ-JP:2823",
    ]


def test_case_refs_skip_the_judgments_own_cases():
    own = extract.own_refs(["S.B. Civil Writ Petition No. 4298/2025"])
    assert not any(r.startswith("CW/4298/2025") for r in extract.case_refs(BODY, own))


TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://law:law@localhost:5432/law_help_test")


def _row(link, bench, case, title, decided, **extra):
    case_type, number, year = case.split("/")
    return {
        "source": "test", "court": "Rajasthan High Court", "bench": bench, "cnr": None,
        "pdf_link": link, "pdf_key": f"data/pdf/{link}", "case_type": case_type,
        "case_number": int(number), "case_year": int(year), "title": f"{case} of {title}",
        "petitioner": None, "respondent": None, "judges": [], "bench_strength": None,
        "disposal_nature": None, "date_of_registration": None, "decision_date": decided,
        "description": None, **extra,
    }


@pytest.fixture
def conn(monkeypatch):
    try:
        conn = db.connect(TEST_URL)
    except Exception:
        pytest.skip("no test database available")
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    db.init_schema(conn)
    conn.execute("TRUNCATE judgments, citations")
    rows = [
        _row("a", "jodhpur", "CW/4298/2025", "SHARVAN CHOUDHARY Vs STATE", "2025-05-08"),
        _row("b", "jaipur", "CW/4298/2025", "MOHAN Vs STATE", "2025-06-01"),    # same number, other bench
        _row("c", "jaipur", "CW/10564/2023", "SANJAY JAIN Vs STATE", "2024-05-31"),  # not Radha: no link
        _row("d1", "jaipur", "CRLMB/812/2024", "KALU Vs STATE", "2024-01-15"),   # interim order
        _row("d2", "jaipur", "CRLMB/812/2024", "KALU Vs STATE", "2024-02-01"),   # final order
        _row("e", "jaipur", "SAW/61/2024", "STATE Vs VINOD", "2024-04-05"),
        _row("f", "jodhpur", "SAW/61/2024", "STATE Vs VINOD", "2024-04-05"),
        _row("g", "jaipur", "CW/5/2024", "RAM Vs STATE", "2024-03-01"),
        _row("x1", "jaipur", "CW/9999/2025", "CITING Vs STATE", "2025-09-01"),
        _row("x2", "jaipur", "CW/9998/2025", "CONNECTED Vs STATE", "2025-09-01"),
    ]
    with conn.cursor() as cur:
        cur.executemany(UPSERT_SQL, rows)
        refs = extract.case_refs(BODY, set(), extract.cases_cited(BODY))
        cur.execute("UPDATE judgments SET case_refs = %s, full_text = %s WHERE pdf_link IN ('x1', 'x2')",
                    (refs, BODY))
        cur.execute("UPDATE judgments SET neutral_citation = '2024:RJ-JP:2823-DB' WHERE pdf_link = 'g'")
        cur.execute("UPDATE judgments SET neutral_citation = '2025:RJ-JP:40000' WHERE pdf_link LIKE 'x%'")
    conn.commit()
    yield conn
    conn.close()


def _ids(conn):
    return {r["pdf_link"]: r["id"] for r in conn.execute("SELECT id, pdf_link FROM judgments")}


def test_link_citations_picks_the_right_bench_and_final_order(conn):
    link_citations(conn)
    ids = {v: k for k, v in _ids(conn).items()}
    cited = {ids[r["cited_id"]] for r in conn.execute(
        "SELECT cited_id FROM citations WHERE citing_id = (SELECT id FROM judgments WHERE pdf_link = 'x1')")}
    assert cited == {"a", "d2", "f", "g"}
    assert link_citations(conn) == 8     # rebuilt, not doubled: x1 and x2 cite the same four


def test_api_lists_cited_by_once_per_common_order(conn):
    from fastapi.testclient import TestClient

    from law_help.api import app

    link_citations(conn)
    ids = _ids(conn)
    client = TestClient(app)
    cited = client.get(f"/judgments/{ids['a']}").json()
    assert cited["cited_by_total"] == 1
    assert cited["cited_by"][0]["id"] == ids["x1"] and cited["cited_by"][0]["connected"] == 1
    citing = client.get(f"/judgments/{ids['x1']}").json()
    assert [c["id"] for c in citing["cites"]] == [ids["a"], ids["f"], ids["g"], ids["d2"]]
    assert client.get(f"/judgments/{ids['b']}").json()["cited_by"] == []
