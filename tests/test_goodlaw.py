"""Good law check: finding what a later judgment set aside, recalled or overruled."""

import os
from datetime import date

import pytest

from law_help import db, goodlaw
from law_help.importer import UPSERT_SQL, link_citations

APPEAL = """1. This intra court appeal is directed against the order dated 05.04.2023 passed by the
learned Single Judge in S.B. Civil Writ Petition No.15358/2022.
2. By impugned order, learned Single Judge disposed off the writ petition as infructuous.
3. Learned counsel for the appellant prayed that the order dated 05.04.2023 be set aside.
4. We accordingly set aside the order dated 05.04.2023 and restore the writ petition to its
original number for consideration on merits.
"""


def test_dates():
    assert goodlaw.dates("orders dated 05.04.2023, 5/4/2023 and 2nd June, 2023") == [
        date(2023, 4, 5), date(2023, 4, 5), date(2023, 6, 2)]
    assert goodlaw.dates("dated 31.02.2023") == []


def test_appeal_sets_aside_the_named_order():
    found = goodlaw.orders_set_aside(APPEAL)
    assert [(t["kind"], t["dates"], t["cases"]) for t in found] == [
        ("set_aside", [date(2023, 4, 5)], ["CW/15358/2022"])]
    assert found[0]["quote"].startswith("We accordingly set aside")


def test_impugned_order_without_a_date_takes_the_appeals_date():
    body = APPEAL.split("3.")[0] + "3. Resultantly, appeal is allowed and the order passed by learned Single Judge is set aside."
    [t] = goodlaw.orders_set_aside(body)
    assert t["dates"] == [date(2023, 4, 5)] and t["cases"] == ["CW/15358/2022"]


@pytest.mark.parametrize("closing", [
    "3. We find no ground to set aside the order dated 05.04.2023, and the appeal is dismissed.",
    "3. Counsel submitted that the order dated 05.04.2023 is liable to be set aside.",
    "3. The appellant is at liberty to apply again if the order dated 05.04.2023 is set aside.",
    "3. The learned Single Judge set aside the order dated 05.04.2023 of the Collector.",
])
def test_no_flag_without_the_courts_own_words(closing):
    assert goodlaw.orders_set_aside(APPEAL.split("3.")[0] + closing) == []


def test_part_of_an_order_is_partly_set_aside():
    body = ("1. The appeals are allowed and paragraph 11 of the order dated 28.11.2023 passed by the "
            "learned Single Judge is set aside.")
    assert [t["kind"] for t in goodlaw.orders_set_aside(body)] == ["partly_set_aside"]
    body = ("1. With the aforesaid observations, this appeal is allowed and the order dated 28.11.2023 "
            "passed by the learned Single Judge is set aside.")
    assert [t["kind"] for t in goodlaw.orders_set_aside(body)] == ["set_aside"]


def test_review_recalls_an_order():
    body = ("1. Considering the above, the order dated 08.07.2024 passed in S.B. Civil Writ Petition "
            "No. 10513/2024 stands recalled and the writ petition is restored to its original number.")
    [t] = goodlaw.orders_set_aside(body, review=True)
    assert (t["kind"], t["dates"], t["cases"]) == ("recalled", [date(2024, 7, 8)], ["CW/10513/2024"])


def test_an_unreadable_case_number_is_still_named():
    body = "1. The order dated 07.11.2023 stands recalled and SB Civil Misc No.8602/2023 is restored."
    [t] = goodlaw.orders_set_aside(body, review=True)
    assert t["cases"] == [] and t["named"]


def test_overrulings():
    body = ("1. The view taken in Kheta Ram v. State (S.B. Civil Writ Petition No.6863/2014) does not "
            "lay down the correct law and is overruled.\n"
            "2. Counsel contended that Ram v. State [2024:RJ-JP:2823] is overruled.\n"
            "3. Office objections are overruled.\n")
    [t] = goodlaw.overrulings(body)
    assert t["cases"] == ["CW/6863/2014"]


def test_party_names_and_same_party():
    assert goodlaw.party_names("SMT. SONAL SHARMA C/O SHRI DINESH KUMAR SHARMA") == (
        ["sonal", "sharma"], ["dinesh", "sharma"])
    same = lambda a, b: goodlaw._same_party({"petitioner": a, "respondent": "STATE OF RAJASTHAN"},
                                            {"petitioner": "STATE OF RAJASTHAN", "respondent": b})
    assert same("KAMAL KISHORE SANKHLA S/O SHRI RAMJI LAL", "KAMAL KISHOR SANKHLA SON OF RAMJI LAL SANKHLA")
    assert same("KARAN SINGH SON OF SHRI SHIMBHU DAYAL", "KARAN SINGH S/O SHIMBHU")
    assert not same("JAI KISHAN MEENA S/O RAMAWTAR MEENA", "LOKESH KUMAR MEENA S/O HARI KISHAN MEENA")
    assert not same("SONAL SHARMA C/O DINESH KUMAR SHARMA", "VIPUL KUMAR SHARMA S/O DINESH KUMAR SHARMA")
    assert not same("JOGA RAM", "JOGA RAM")   # one name alone could be anyone


TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://law:law@localhost:5432/law_help_test")


def _row(link, case, petitioner, respondent, decided, strength="single", **extra):
    case_type, number, year = case.split("/")
    return {
        "source": "test", "court": "Rajasthan High Court", "bench": "jaipur", "cnr": None,
        "pdf_link": link, "pdf_key": f"data/pdf/{link}", "case_type": case_type,
        "case_number": int(number), "case_year": int(year),
        "title": f"{case} of {petitioner} Vs {respondent}", "petitioner": petitioner,
        "respondent": respondent, "judges": [], "bench_strength": strength,
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
    conn.execute("TRUNCATE judgments, citations, treatments")
    state = "STATE OF RAJASTHAN"
    rows = [
        _row("w1", "CW/15358/2022", "MOHAN LAL", state, "2023-04-05"),         # named by number
        _row("w2", "CW/1641/2024", "PRAVEEN BHADANA S/O RAM CHAND", state, "2024-02-14"),
        _row("w3", "CW/1700/2024", "PRAVEEN KUMAR S/O RAM CHAND", state, "2024-02-14"),  # not him
        _row("w4", "CW/6863/2014", "KHETA RAM", state, "2015-03-12"),
        _row("w5", "CW/99/2023", "SITA DEVI", state, "2023-05-01"),           # upheld: no flag
        _row("a1", "SAW/336/2023", state, "MOHAN LAL", "2024-01-03", "division"),
        _row("a2", "SAW/221/2024", "PRAVEEN BHADANA S/O SHRI RAM CHAND", state, "2024-08-02", "division"),
        _row("a3", "SAW/400/2023", state, "SITA DEVI", "2024-02-01", "division"),
        _row("f1", "CW/5000/2020", "RAM", state, "2024-06-01", "full"),
    ]
    with conn.cursor() as cur:
        cur.executemany(UPSERT_SQL, rows)
    texts = {
        "a1": (APPEAL, "Allowed"),
        "a2": ("1. Appellant has preferred this appeal aggrieved by judgment dated 14.02.2024 passed by "
               "learned Single Judge in the writ petition.\n2. Resultantly, appeal is allowed and the order "
               "passed by the learned Single Judge is set aside.", "Allowed"),
        "a3": ("1. The appeal is directed against the order dated 01.05.2023 passed by the learned Single "
               "Judge.\n2. We find no reason to set aside the order dated 01.05.2023. Dismissed.", "Dismissed"),
        "f1": ("1. The judgment in Kheta Ram v. State (S.B. Civil Writ Petition No.6863/2014) decided on "
               "12.03.2015 does not lay down the correct law and is overruled.", "Disposed of"),
    }
    for link, (text, outcome) in texts.items():
        refs = ["CW/6863/2014/kheta/"] if link == "f1" else []
        conn.execute("UPDATE judgments SET full_text = %s, text_language = 'en', outcome = %s, "
                     "case_refs = %s WHERE pdf_link = %s", (text, outcome, refs, link))
    conn.commit()
    yield conn
    conn.close()


def _ids(conn):
    return {r["pdf_link"]: r["id"] for r in conn.execute("SELECT id, pdf_link FROM judgments")}


def test_link_treatments(conn):
    link_citations(conn)
    assert goodlaw.link_treatments(conn) == 3
    ids = _ids(conn)
    got = {(r["judgment_id"], r["by_id"], r["kind"])
           for r in conn.execute("SELECT * FROM treatments")}
    assert got == {
        (ids["w1"], ids["a1"], "set_aside"),
        (ids["w2"], ids["a2"], "set_aside"),
        (ids["w4"], ids["f1"], "overruled"),
    }


def test_api_shows_the_flag(conn):
    from fastapi.testclient import TestClient

    from law_help.api import app

    link_citations(conn)
    goodlaw.link_treatments(conn)
    conn.commit()
    ids = _ids(conn)
    client = TestClient(app)
    j = client.get(f"/judgments/{ids['w2']}").json()
    assert [(t["id"], t["kind"]) for t in j["treated_by"]] == [(ids["a2"], "set_aside")]
    assert j["treated_by"][0]["quote"].startswith("Resultantly")
    assert j["good_law"] == "set_aside"
    results = {r["id"]: r["good_law"] for r in client.get("/judgments?q=praveen").json()["results"]}
    assert results[ids["w2"]] == "set_aside" and results[ids["w3"]] is None
