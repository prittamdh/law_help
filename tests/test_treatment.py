"""How a citing judgment treats the judgments it cites: followed, distinguished, doubted or cited."""

import os

import pytest

from law_help import db, extract, treatment
from law_help.importer import UPSERT_SQL, link_citations


@pytest.mark.parametrize("sentence, label", [
    # followed / relied on
    ("The controversy involved in the present petition is squarely covered by the judgment of this Court "
     "in Kheta Ram v. State (S.B. Civil Writ Petition No.6863/2014) decided on 12.03.2015.", "followed"),
    ("In view of the law laid down in Kheta Ram (supra), the writ petition is allowed.", "followed"),
    ("This Court respectfully follows the view taken in Kheta Ram (supra).", "followed"),
    ("This Court has relied upon the judgment in Kheta Ram (supra) while deciding the batch.", "followed"),
    ("The facts of the present case are on all fours with Kheta Ram (supra).", "followed"),
    ("The case of Kheta Ram (supra) cannot be distinguished on facts.", "followed"),
    # distinguished
    ("The judgment in Kheta Ram (supra) relied upon by learned counsel for the respondents is clearly "
     "distinguishable on facts.", "distinguished"),
    ("The said judgment is not applicable to the facts of the present case.", "distinguished"),
    ("The decision in Kheta Ram (supra) has no application where the appointment was never regularised.",
     "distinguished"),
    ("The present case is not squarely covered by Kheta Ram (supra).", "distinguished"),
    ("The facts in Kheta Ram (supra) were entirely different.", "distinguished"),
    # doubted / not followed
    ("With respect, the view taken in Kheta Ram (supra) is not followed.", "doubted"),
    ("The judgment in Kheta Ram (supra) has not been followed by the Division Bench.", "doubted"),
    ("We are unable to agree with the view taken in Kheta Ram (supra).", "doubted"),
    ("Kheta Ram (supra), rendered without noticing the Constitution Bench, is per incuriam.", "doubted"),
    ("The correctness of Kheta Ram (supra) was doubted in a later reference.", "doubted"),
    ("Kheta Ram (supra) is no longer good law.", "doubted"),
    # just cited: counsel's reliance and arguments, other senses of the words
    ("Learned counsel for the petitioner has relied upon the judgment in Kheta Ram v. State.", None),
    ("Learned counsel for the State submitted that the judgment in Kheta Ram (supra) is distinguishable.", None),
    ("In Kheta Ram (supra), the Apex Court relied upon its earlier view.", None),
    ("It cannot be doubted that the petitioner is entitled to pension.", None),
    ("The preliminary objection is overruled.", None),
    ("The appeal was dismissed, followed by a review petition.", None),
    ("Kheta Ram v. State was cited at the Bar.", None),
])
def test_classify(sentence, label):
    assert treatment.classify(sentence) == label


def _cited(i, case, petitioner, respondent, **extra):
    case_type, number, year = case.split("/")
    return {"id": i, "case_type": case_type, "case_number": int(number), "case_year": int(year),
            "petitioner": petitioner, "respondent": respondent, "neutral_citation": None,
            "report_citation": None, "title": None, **extra}


def test_treatments_keep_each_case_to_its_own_words():
    text = (
        "1. Learned counsel for the petitioner has relied upon Kheta Ram v. State of Rajasthan "
        "(S.B. Civil Writ Petition No.6863/2014) decided on 12.03.2015 and Mohan Lal v. State "
        "(S.B. Civil Writ Petition No.15358/2022) decided on 05.04.2023.\n"
        "2. Learned counsel for the respondents cited Sita Devi v. State [2024:RJ-JP:2823].\n"
        "3. The judgment in Kheta Ram (supra) is distinguishable, while the present case is squarely "
        "covered by Mohan Lal (supra).\n"
        "4. The view taken in Sita Devi v. State is per incuriam; it is not followed.\n"
        "5. Gopal v. Union of India (S.B. Civil Writ Petition No.99/2020) was decided on 01.01.2021. "
        "The present case stands on a different footing.\n"
        "6. Hari Singh v. State (S.B. Civil Writ Petition No.77/2019) decided on 02.02.2020. "
        "The ratio of that judgment is binding on this Court.\n"
        "7. Notice was issued in Ramesh v. State (S.B. Civil Writ Petition No.55/2018) on 03.03.2018.\n"
    )
    cited = [
        _cited(1, "CW/6863/2014", "KHETA RAM", "STATE OF RAJASTHAN"),
        _cited(2, "CW/15358/2022", "MOHAN LAL", "STATE"),
        _cited(3, "CW/1/2024", "SITA DEVI", "STATE", neutral_citation="2024:RJ-JP:2823-DB"),
        _cited(4, "CW/99/2020", "GOPAL", "UNION OF INDIA"),
        _cited(5, "CW/77/2019", "HARI SINGH", "STATE"),
        _cited(6, "CW/55/2018", "RAMESH", "STATE"),
    ]
    got = treatment.treatments(text, cited)
    assert {k: v[0] for k, v in got.items()} == {
        1: "distinguished", 2: "followed", 3: "doubted", 4: "distinguished", 5: "followed", 6: "cited"}
    assert got[1][1].startswith("The judgment in Kheta Ram (supra) is distinguishable")
    assert "binding on this Court" in got[5][1]      # the next sentence, which cites nothing else
    assert got[6][1] is None


def test_supreme_court_judgment_found_by_citation():
    text = ("The Supreme Court in Gian Singh v. State of Punjab, 2012 INSC 512, laid down the test. "
            "Having regard to Gian Singh (supra), we are unable to agree with the view that the offence "
            "cannot be compounded.")
    cited = [{"id": 9, "case_type": "Criminal Appeal", "case_number": 1, "case_year": 2012,
              "neutral_citation": "2012 INSC 512", "report_citation": None,
              "petitioner": "GIAN SINGH", "respondent": "STATE OF PUNJAB", "title": None}]
    assert treatment.treatments(text, cited)[9][0] == "doubted"


TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://law:law@localhost:5432/law_help_test")

BODY = """1. The issue stands finally adjudicated by a Co-ordinate Bench of this Court in
Sharvan Choudhary Vs. State of Rajasthan and Ors. : S.B. Civil Writ Petition No.4298/2025,
decided on 08.05.2025. The controversy is squarely covered by that judgment.
2. Learned counsel for the respondents relied on Kalu Vs. State : S.B. Civil Writ Petition
No.812/2024, decided on 01.02.2024. The judgment in Kalu (supra) is distinguishable on facts.
3. Reference was also made to Vinod Vs. State : S.B. Civil Writ Petition No.61/2024 decided on
05.04.2024.
"""


def _row(link, case, title, decided):
    case_type, number, year = case.split("/")
    return {
        "source": "test", "court": "Rajasthan High Court", "bench": "jaipur", "cnr": None,
        "pdf_link": link, "pdf_key": f"data/pdf/{link}", "case_type": case_type,
        "case_number": int(number), "case_year": int(year), "title": f"{case} of {title}",
        "petitioner": None, "respondent": None, "judges": [], "bench_strength": None,
        "disposal_nature": None, "date_of_registration": None, "decision_date": decided,
        "description": None,
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
        _row("a", "CW/4298/2025", "SHARVAN CHOUDHARY Vs STATE", "2025-05-08"),
        _row("b", "CW/812/2024", "KALU Vs STATE", "2024-02-01"),
        _row("c", "CW/61/2024", "VINOD Vs STATE", "2024-04-05"),
        _row("x1", "CW/9999/2025", "CITING Vs STATE", "2025-09-01"),
        _row("x2", "CW/9998/2025", "CONNECTED Vs STATE", "2025-09-01"),
    ]
    with conn.cursor() as cur:
        cur.executemany(UPSERT_SQL, rows)
        refs = extract.case_refs(BODY, set(), extract.cases_cited(BODY))
        cur.execute("UPDATE judgments SET case_refs = %s, full_text = %s WHERE pdf_link IN ('x1', 'x2')",
                    (refs, BODY))
    conn.commit()
    yield conn
    conn.close()


def _ids(conn):
    return {r["pdf_link"]: r["id"] for r in conn.execute("SELECT id, pdf_link FROM judgments")}


def test_link_citations_labels_each_link(conn):
    link_citations(conn)
    ids = {v: k for k, v in _ids(conn).items()}
    got = {(ids[r["citing_id"]], ids[r["cited_id"]]): r["treatment"]
           for r in conn.execute("SELECT * FROM citations")}
    assert got == {
        ("x1", "a"): "followed", ("x1", "b"): "distinguished", ("x1", "c"): "cited",
        ("x2", "a"): "followed", ("x2", "b"): "distinguished", ("x2", "c"): "cited",
    }
    # the schema change and the labelling can both run again
    db.init_schema(conn)
    assert link_citations(conn) == 6
    assert treatment.label_citations(conn, workers=1) == 4
    conn.commit()


def test_api_shows_labels_and_counts(conn):
    from fastapi.testclient import TestClient

    from law_help.api import app

    link_citations(conn)
    conn.commit()
    ids = _ids(conn)
    client = TestClient(app)
    j = client.get(f"/judgments/{ids['b']}").json()
    assert [(c["id"], c["treatment"]) for c in j["cited_by"]] == [(ids["x1"], "distinguished")]
    assert j["cited_by"][0]["treatment_quote"].startswith("The judgment in Kalu (supra) is distinguishable")
    assert j["cited_by_treatments"] == {"distinguished": 1}   # a common order counts once
    assert client.get(f"/judgments/{ids['c']}").json()["cited_by_treatments"] == {"cited": 1}
