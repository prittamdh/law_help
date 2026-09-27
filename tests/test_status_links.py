"""Check case status links (law_help.status_links)."""

from law_help.status_links import ECOURTS_CNR_SEARCH, ESCR, SCI_CASE_NO, SCI_DIARY_NO, status_links

HC = {"court": "Rajasthan High Court", "bench": "jaipur", "cnr": "RJHC020000011994",
      "case_type": "CRLA", "case_number": 28, "case_year": 1994}


def test_rajasthan_with_cnr():
    s = status_links(HC)
    assert s["links"][0] == {"label": "Check case status", "url": ECOURTS_CNR_SEARCH}
    assert s["links"][1]["url"] == ("https://hcservices.ecourts.gov.in/ecourtindiaHC/cases/case_no.php"
                                    "?state_cd=9&dist_cd=1&court_code=2&stateNm=Rajasthan")
    assert s["fields"] == [
        {"label": "CNR", "value": "RJHC020000011994", "copy": True},
        {"label": "Bench", "value": "Jaipur"},
        {"label": "Case type", "value": "CRLA"},
        {"label": "Case number", "value": "28"},
        {"label": "Year", "value": "1994"},
    ]
    assert "CNR" in s["note"]


def test_rajasthan_bench_from_cnr_and_jodhpur_code():
    s = status_links({**HC, "bench": "unknown", "cnr": "rjhc010000021990"})
    assert "court_code=1&" in s["links"][1]["url"]
    assert s["fields"][0]["value"] == "RJHC010000021990"


def test_rajasthan_without_cnr_links_case_number_search():
    s = status_links({**HC, "cnr": None, "bench": "jodhpur"})
    assert [link["label"] for link in s["links"]] == ["Check case status"]
    assert "court_code=1&" in s["links"][0]["url"]
    assert "case type" in s["note"]
    # nothing to search with
    assert status_links({"court": "Rajasthan High Court", "bench": "jaipur", "cnr": "RJ1"}) is None


def test_supreme_court():
    s = status_links({"court": "Supreme Court of India", "bench": "supreme court", "cnr": "ESCR010004822024",
                      "case_type": "CRIMINAL APPEAL", "case_number": 1031, "case_year": 2015,
                      "neutral_citation": "2024 INSC 735"})
    assert [link["url"] for link in s["links"]] == [SCI_CASE_NO, SCI_DIARY_NO, ESCR]
    assert s["fields"][0] == {"label": "Case type", "value": "CRIMINAL APPEAL"}
    assert s["fields"][-1] == {"label": "Neutral citation", "value": "2024 INSC 735", "copy": True}
    # The eSCR record id is not a case status CNR, so it is not offered.
    assert all(f["label"] != "CNR" for f in s["fields"])

    only_citation = status_links({"court": "Supreme Court of India", "neutral_citation": "1950 INSC 1"})
    assert only_citation["links"][0] == {"label": "Check case status", "url": SCI_DIARY_NO}
    assert status_links({"court": "Supreme Court of India"}) is None


def test_unknown_court():
    assert status_links({"court": "Delhi High Court", "cnr": "DLHC010000012020"}) is None
