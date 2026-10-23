"""The committed bare act data and the old-to-new section map."""

import importlib.util
from pathlib import Path

import pytest

from law_help import bareacts


@pytest.mark.parametrize("slug,number,title", [
    ("ipc", "302", "Punishment for murder"),
    ("ipc", "498A", "Husband or relative of husband of a woman subjecting her to cruelty"),
    ("ipc", "376AB", "Punishment for rape on woman under twelve years of age"),
    ("bns", "103", "Punishment for murder"),
    ("crpc", "438", "Direction for grant of bail to person apprehending arrest"),
    ("bnss", "528", "Saving of inherent powers of High Court"),
    ("bnss", "301", "Definitions"),
    ("evidence", "65B", "Admissibility of electronic records"),
    ("bsa", "63", "Admissibility of electronic records"),
    ("cpc", "Order VII Rule 11", "Rejection of plaint"),
    ("constitution", "226", "Power of High Courts to issue certain writs"),
    ("ni-act", "138", "Dishonour of cheque for insufficiency, etc., of funds in the account"),
])
def test_sections_have_their_titles(slug, number, title):
    sec = bareacts.get_section(slug, number)
    assert sec["title"] == title
    assert len(sec["text"]) > 40


def test_every_act_is_complete_and_in_order():
    for slug, act in bareacts.acts().items():
        numbers = [s["number"] for s in act["sections"] if not s["number"].startswith("Order")]
        assert len(numbers) == len(set(numbers)), slug
        assert numbers[0] == "1", slug
    assert len(bareacts.acts()["bns"]["sections"]) == 358
    assert len(bareacts.acts()["bnss"]["sections"]) == 531
    assert len(bareacts.acts()["bsa"]["sections"]) == 170


# Well-known pairs, checked against published tables.
@pytest.mark.parametrize("old_act,old,new_act,new", [
    ("ipc", "302", "bns", "103"), ("ipc", "420", "bns", "318(4)"), ("ipc", "498A", "bns", "85"),
    ("ipc", "376AB", "bns", "65(2)"), ("ipc", "34", "bns", "3(5)"), ("ipc", "307", "bns", "109"),
    ("crpc", "438", "bnss", "482"), ("crpc", "439", "bnss", "483"), ("crpc", "482", "bnss", "528"),
    ("crpc", "154", "bnss", "173"), ("crpc", "161", "bnss", "180"), ("crpc", "164", "bnss", "183"),
    ("crpc", "125", "bnss", "144"), ("crpc", "313", "bnss", "351"), ("crpc", "41A", "bnss", "35"),
    ("crpc", "397", "bnss", "438"), ("crpc", "389", "bnss", "430"), ("crpc", "57", "bnss", "58"),
    ("evidence", "65B", "bsa", "63"), ("evidence", "27", "bsa", "23"), ("evidence", "32", "bsa", "26"),
    ("evidence", "113B", "bsa", "118"), ("evidence", "45", "bsa", "39"), ("evidence", "114", "bsa", "119"),
])
def test_old_to_new(old_act, old, new_act, new):
    assert new in [e["ref"] for e in bareacts.equivalents(old_act, old) if e["act"] == new_act]
    back = bareacts.equivalents(new_act, bareacts.base_number(new))
    assert old in [e["ref"] for e in back if e["act"] == old_act]


def test_dropped_sections_say_so():
    assert [e["ref"] for e in bareacts.equivalents("ipc", "377")] == [None]


def test_search_forms_cover_sub_sections_and_the_other_code():
    forms, added = bareacts.search_forms("Indian Penal Code, 1860", "302")
    assert {"act": "Bharatiya Nyaya Sanhita, 2023", "sections": ["103(1)"]} in forms
    assert [e["ref"] for e in added] == ["103"]
    forms, _ = bareacts.search_forms("Code of Civil Procedure, 1908", "Order VII Rule 11")
    assert [f["sections"] for f in forms] == [["Order VII Rule 11"], ["Order 7 Rule 11"]]
    forms, added = bareacts.search_forms("Dowry Prohibition Act, 1961", "4")
    assert forms == [{"act": "Dowry Prohibition Act, 1961", "sections": ["4"]}] and added == []


# The four criminal acts added after the first twelve. Their JSON is built from India Code PDFs by
# scripts/build_bare_acts.py; until it is, only the wiring is checked.
BUILDER = Path(__file__).resolve().parent.parent / "scripts" / "build_bare_acts.py"
NEW_ACTS = {"ndps": "NDPS Act", "pocso": "POCSO Act", "sc-st-act": "SC/ST Act", "arms-act": "Arms Act"}


@pytest.mark.parametrize("slug,short,cited", [
    ("ndps", "NDPS Act", "N.D.P.S. Act"), ("pocso", "POCSO Act", "POCSO Act, 2012"),
    ("sc-st-act", "SC/ST Act", "SC/ST (Prevention of Atrocities) Act"), ("arms-act", "Arms Act", "Arms Act"),
])
def test_new_acts_are_registered_under_the_name_judgments_use(slug, short, cited):
    spec = importlib.util.spec_from_file_location("build_bare_acts", BUILDER)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    ACTS = builder.ACTS
    from law_help.extract import canonical_act

    row = next(a for a in ACTS if a[0] == slug)
    assert row[1] == canonical_act(cited) and row[2] == short
    assert row[5] == "India Code"
    assert slug in bareacts.ORDER


@pytest.mark.parametrize("slug,number,title", [
    ("ndps", "37", "Offences to be cognizable and non-bailable"),
    ("pocso", "4", "Punishment for penetrative sexual assault"),
    ("arms-act", "25", "Punishment for certain offences"),
])
def test_new_acts_text(slug, number, title):
    if slug not in bareacts.acts():
        pytest.skip(f"{slug}.json not built yet")
    act = bareacts.acts()[slug]
    assert act["short"] == NEW_ACTS[slug] and act["source"].startswith("India Code")
    assert bareacts.slug_for(act["act"]) == slug
    sec = bareacts.get_section(slug, number)
    assert sec["title"] == title and len(sec["text"]) > 40
