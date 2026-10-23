"""Kruti Dev to Unicode conversion, on text copied from real Rajasthan HC orders."""

import os

import pytest

from law_help import db, extract, importer, krutidev
from law_help.importer import UPSERT_SQL

# RJHC020621762024_1_2024-07-26.pdf as pdfium extracts it: English header, Kruti Dev body.
ORDER = """[2024:RJ-JP:31680]
HIGH COURT OF JUDICATURE FOR RAJASTHAN
BENCH AT JAIPUR
S.B. Criminal Appeal (Sb) No. 1576/2024
Manish Sain S/o Santosh Kumar Sain, Aged About 38
----Appellant
Versus
1. State Of Rajasthan, Through Pp
----Respondents
For Appellant(s) : Mr. Rajat SIngh Slolanki, Adv.
For Respondent(s) : Mr. Imran Khan, PP
HON'BLE MR. JUSTICE UMA SHANKER VYAS
Judgment / Order
26/07/2024
dqN le; i'pkr uohu vkosnu is'k djus dh Lora=rk
ds lkFk vihykFkhZ ds fo}ku vf/koDrk bl tekur vihy dks
okfil ysuk pkgrs gSaA
QyLo:i] ;g tekur vihy okfil fy;s tkus ds
vk/kkj ij] vihykFkhZ dks mijksDr Lora=rk iznku djrs gq,]
[kkfjt dh tkrh gSA
(UMA SHANKER VYAS),J
Murari Lal Sharma /456"""


@pytest.mark.parametrize("kruti, hindi", [
    ("izkFkhZ&vfHk;qDr dh vksj ls", "प्रार्थी-अभियुक्त की ओर से"),
    ("vUrxZr /kkjk 439 n.M izfØ;k lafgrk", "अन्तर्गत धारा 439 दण्ड प्रक्रिया संहिता"),
    ("la[;k 134@2023] vijk/k", "संख्या 134/2023, अपराध"),
    ("ifjfLFkfr;ksa", "परिस्थितियों"),               # ि moved past a half letter
    ("okf.kfT;d", "वाणिज्यिक"),
    ("nf'kZr", "दर्शित"),                            # ि and reph on one letter
    ("jftLVªkj vkWQ dEiuht", "रजिस्ट्रार ऑफ कम्पनीज"),
    ("fnukad 27-03-2021", "दिनांक 27.03.2021"),
    ("fHkokMh+", "भिवाड़ी"),                          # nukta after the vowel sign in the PDF
])
def test_to_unicode(kruti, hindi):
    assert krutidev.to_unicode(kruti) == hindi


def test_rejoins_words_the_pdf_split_before_a_vowel_sign():
    assert krutidev.to_unicode("tekur vko snu i= dk s ugh a") == "जमानत आवेदन पत्र को नहीं"
    assert krutidev.to_unicode("mi;qZDr rdkZ sa") == "उपर्युक्त तर्कों"


def test_convert_mixed_keeps_the_english_header():
    out = krutidev.convert_mixed(ORDER)
    head, body = out.split("26/07/2024\n")
    assert head == ORDER.split("26/07/2024\n")[0]
    assert body.startswith("कुछ समय पश्चात नवीन आवेदन पेश करने की स्वतंत्रता\n")
    assert "वापिस लेना चाहते हैं।" in body
    assert "फलस्वरूप, यह जमानत अपील" in body
    assert body.endswith("खारिज की जाती है।\n(UMA SHANKER VYAS),J\nMurari Lal Sharma /456")


def test_convert_mixed_keeps_bracketed_english_and_converts_advocate_names():
    text = ("For Petitioner(s) : Jh vfuy dqekj tSu\n"
            "dEiuh dh o\"kZ 2018&19 ds foRrh; fLFkfr fooj.k (Balance Sheet) esa\n"
            "lkekU; lHkkvksa (Annual General\nMeetings) esa Hkkx ugha fy;k")
    assert krutidev.convert_mixed(text) == (
        "For Petitioner(s) : श्री अनिल कुमार जैन\n"
        "कम्पनी की वर्ष 2018-19 के वित्तीय स्थिति विवरण (Balance Sheet) में\n"
        "सामान्य सभाओं (Annual General\nMeetings) में भाग नहीं लिया")


def test_readable_and_extract_on_a_converted_order():
    text, original = extract.readable(ORDER)
    assert original == ORDER
    out = extract.extract(text)
    assert out["language"] == "hi"
    assert out["judges"] == ["UMA SHANKER VYAS"]
    assert out["advocates"]["respondent"] == ["Mr. Imran Khan, PP"]
    assert out["outcome"] == "Withdrawn"
    assert out["summary"].startswith("कुछ समय पश्चात")
    assert extract.readable("An English order.") == ("An English order.", None)


def test_hindi_outcomes():
    assert extract.hindi_outcome("अतः जमानत आवेदन पत्र स्वीकार किया जाकर आदेश दिया जाता है कि अभियुक्त को "
                                 "जमानत पर रिहा किया जावे।") == "Bail granted"
    assert extract.hindi_outcome("परिणामतः प्रार्थी की ओर से प्रस्तुत यह जमानत आवेदन पत्र निरस्त किया "
                                 "जाता है।") == "Bail refused"


TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://law:law@localhost:5432/law_help_test")


def test_hindi_command_converts_stored_text_and_hindi_search_finds_it(monkeypatch):
    try:
        conn = db.connect(TEST_URL)
    except Exception:
        pytest.skip("no test database available")
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    with conn:
        db.init_schema(conn)
        conn.execute("TRUNCATE judgments")
        for i, full_text in enumerate([ORDER, "An English order about bail."]):
            conn.execute(UPSERT_SQL, {
                "source": "test", "court": "Rajasthan High Court", "bench": "jaipur", "cnr": None,
                "pdf_link": f"test/{i}.pdf", "pdf_key": f"data/pdf/test/{i}.pdf", "case_type": "CRLAS",
                "case_number": i, "case_year": 2024, "title": "t", "petitioner": None,
                "respondent": None, "judges": [], "bench_strength": None, "disposal_nature": None,
                "date_of_registration": None, "decision_date": None, "description": None,
            })
            conn.execute("UPDATE judgments SET full_text = %s, text_language = %s WHERE case_number = %s",
                         (full_text, extract.language(full_text), i))
        conn.commit()

        assert importer.hindi(workers=1) == 1
        assert importer.hindi(workers=1) == 1              # re-converts from the original
        row = conn.execute("SELECT * FROM judgments WHERE case_number = 0").fetchone()
        assert row["full_text_original"] == ORDER
        assert row["text_language"] == "hi" and row["outcome"] == "Withdrawn"
        assert "जमानत अपील" in row["full_text"]
        hit = conn.execute("SELECT case_number FROM judgments "
                           "WHERE search @@ websearch_to_tsquery('english', 'जमानत अपील')").fetchall()
        assert [r["case_number"] for r in hit] == [0]


def test_text_extraction_converts_a_kruti_dev_pdf():
    from law_help import text
    from tests.test_text import make_pdf

    parsed = text.parse_pdf(make_pdf(*ORDER.split("\n")))
    assert parsed["original"].startswith("[2024:RJ-JP:31680]")
    assert "जमानत अपील" in parsed["text"]
    assert parsed["fields"]["language"] == "hi"
