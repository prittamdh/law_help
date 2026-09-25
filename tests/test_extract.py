"""Unit tests for law_help.extract on judgment text laid out like a Rajasthan HC PDF.

The accuracy numbers on real judgments come from eval/evaluate.py, which needs the network.
"""

import os

import pytest

from law_help import db, extract
from law_help.importer import UPSERT_SQL, structure

ORDER = """[2024:RJ-JD:12345]
HIGH COURT OF JUDICATURE FOR RAJASTHAN AT
JODHPUR
S.B. Criminal Misc Temporary Suspension Of Sentence
Application (Appeal) No. 675/2024
1. Ramesh Kumar S/o Shri Mohan Lal, Aged About 30 Years, R/o Village
Kheda, District Pali. ----Petitioners
Versus
1. State Of Rajasthan, Through PP
2. Rajasthan Tourism Development Corporation, Paryatan Bhawan, Iiird Floor, Jaipur
----Respondents
For Petitioner(s) : Mr. A.B. Sharma for
Mr. C.D. Verma
For : Mr. E.F. Khan, PP
Respondent(s)
HON'BLE THE ACTING CHIEF JUSTICE MR. GANESH PRASAD
HON'BLE DR. JUSTICE PUSHPA RANI
Order
23/05/2024
1. This criminal misc. petition under Section 482 Cr.P.C. has been preferred for quashing
FIR No.166/2023 registered for offences under Sections 354, 376 & 120-B IPC and Sections
8/15 of the NDPS Act.
[2024:RJ-JD:12345] (2 of 2) [SOSA-675/2024]
2. Reliance is placed on Gian Singh Vs. State of Punjab & Anr. reported in (2012) 10 SCC
303 and State of Haryana & Ors. v. Bhajan Lal [AIR 1992 SC 604], and Article 21 of the
Constitution. The matter also pertains to the Right to Information Act, 2005.
3. Accordingly, the petition is allowed and the FIR is quashed.
4. Stay application also stands disposed of.
(PUSHPA RANI),J (GANESH PRASAD),ACTING CJ
12-Clerk/-
"""


def test_header():
    h = extract.header(ORDER)
    assert h["cases"] == ["S.B. Criminal Misc Temporary Suspension Of Sentence "
                          "Application (Appeal) No. 675/2024"]
    assert h["petitioners"] == ["Ramesh Kumar"]
    assert h["respondents"] == ["State Of Rajasthan, Through PP",
                                "Rajasthan Tourism Development Corporation"]
    assert h["advocates"] == {"petitioner": ["Mr. A.B. Sharma", "Mr. C.D. Verma"],
                              "respondent": ["Mr. E.F. Khan, PP"]}
    assert h["judges"] == ["GANESH PRASAD", "PUSHPA RANI"]
    assert h["order_date"] == "2024-05-23"


def test_neutral_citation():
    assert extract.neutral_citation(ORDER) == "2024:RJ-JD:12345"
    assert extract.neutral_citation("[2023/RJJD/014204]\nHIGH COURT") == "2023:RJ-JD:14204"
    assert extract.neutral_citation("HIGH COURT OF JUDICATURE") is None


def test_acts_cited():
    acts = {a["act"]: a["sections"] for a in extract.acts_cited(extract.body(ORDER))}
    assert acts == {
        "Code of Criminal Procedure, 1973": ["482"],
        "Constitution of India": ["21"],
        "Indian Penal Code, 1860": ["120B", "354", "376"],
        "Narcotic Drugs and Psychotropic Substances Act, 1985": ["8", "15"],
        "Right to Information Act, 2005": [],
    }


@pytest.mark.parametrize("text, expected", [
    ("under Order 39 Rule 1 & 2 CPC", {"Code of Civil Procedure, 1908": ["Order 39 Rule 1", "Order 39 Rule 2"]}),
    ("under 143, 341 & 120-B IPC", {"Indian Penal Code, 1860": ["120B", "143", "341"]}),
    ("Sections 3(1)(r)(s) and 3(2)(va) of the SC/ST Act",
     {"Scheduled Castes and Scheduled Tribes (Prevention of Atrocities) Act, 1989": ["3(1)(r)(s)", "3(2)(va)"]}),
    ("Section 13 of the Rajasthan Tenancy Act, 1955", {"Rajasthan Tenancy Act, 1955": ["13"]}),
    ("the application under Section 439", {}),              # no act named: left out, not guessed
    ("Special Judge (POCSO Act Cases), Pali", {}),           # a court's name, not a citation
    ("FIR No. 80/2023 IPC", {}),
])
def test_acts_cited_forms(text, expected):
    assert {a["act"]: a["sections"] for a in extract.acts_cited(text)} == expected


def test_cases_cited():
    cases = extract.cases_cited(extract.body(ORDER), "2024:RJ-JD:12345")
    assert cases == [
        {"name": "Gian Singh v. State of Punjab & Anr.", "citations": ["(2012) 10 SCC 303"]},
        {"name": "State of Haryana & Ors. v. Bhajan Lal", "citations": ["AIR 1992 SC 604"]},
    ]


def test_summary_skips_boilerplate_and_prefers_a_real_outcome():
    s = extract.summary(extract.body(ORDER))
    assert s.startswith("This criminal misc. petition under Section 482 Cr.P.C. has been preferred")
    assert s.endswith("Accordingly, the petition is allowed and the FIR is quashed.")


def test_language():
    assert extract.language(ORDER) == "en"
    kruti = "izkFkhZ dh vksj ls ;g tekur izkFkZuk i= gS vkSj fd;k x;k gS " * 5
    assert extract.language(kruti) == "hi-krutidev"
    assert extract.language("(Downloaded on 25/01/2023)\n" * 3) == "no-text"


def test_extract_skips_body_fields_for_legacy_hindi():
    text = ORDER.replace("1. This criminal", "izkFkhZ dh vksj ls ;g tekur gS vkSj fd;k dk ds esa rFkk " * 5)
    out = extract.extract(text)
    assert out["language"] == "hi-krutidev"
    assert out["judges"] == ["GANESH PRASAD", "PUSHPA RANI"]
    assert out["acts_cited"] == [] and out["summary"] is None


TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://law:law@localhost:5432/law_help_test")


def test_structure_command_fills_columns(monkeypatch):
    try:
        conn = db.connect(TEST_URL)
    except Exception:
        pytest.skip("no test database available")
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    with conn:
        db.init_schema(conn)
        conn.execute("TRUNCATE judgments")
        conn.execute(UPSERT_SQL, {
            "source": "test", "court": "Rajasthan High Court", "bench": "jodhpur", "cnr": None,
            "pdf_link": "test/s.pdf", "pdf_key": "data/pdf/test/s.pdf", "case_type": "SOSA",
            "case_number": 675, "case_year": 2024, "title": "t", "petitioner": None,
            "respondent": None, "judges": [], "bench_strength": None, "disposal_nature": None,
            "date_of_registration": None, "decision_date": None, "description": None,
        })
        conn.execute("UPDATE judgments SET full_text = %s", (ORDER,))
        conn.commit()

        assert structure(limit=None, redo=False) == 1
        assert structure(limit=None, redo=False) == 0      # already at the current version
        row = conn.execute("SELECT * FROM judgments").fetchone()
        assert row["neutral_citation"] == "2024:RJ-JD:12345"
        assert row["bench_judges"] == ["GANESH PRASAD", "PUSHPA RANI"]
        assert row["extractor_version"] == extract.EXTRACTOR_VERSION
        hit = conn.execute(
            "SELECT count(*) AS n FROM judgments WHERE acts_cited @> %s::jsonb",
            ('[{"act": "Indian Penal Code, 1860", "sections": ["376"]}]',),
        ).fetchone()
        assert hit["n"] == 1
