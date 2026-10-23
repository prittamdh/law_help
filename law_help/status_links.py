"""Where to check a case's live status on the court's own site ("Check case status").

None of these sites can be deep-linked to a result: eCourts and the Supreme Court both ask for
the CNR or the case number and a captcha. So we link to the right search page, with the court
preselected where the page takes it from the URL, and give the values to type in.

The URLs are from the sites' public pages; they could not be fetched from the build sandbox,
so check them here first if a link stops working.
"""

import re

from .supreme import COURT_NAME as SUPREME_COURT

# eCourts High Court services. Rajasthan is state_cd 9; court_code 1 is the principal seat at
# Jodhpur and 2 the Jaipur bench. The old ecourtindiaHC pages take these from the query string.
ECOURTS_HC = "https://hcservices.ecourts.gov.in/ecourtindiaHC"
ECOURTS_CNR_SEARCH = "https://hcservices.ecourts.gov.in/hcservices/main.php"  # CNR box on the home page
RAJASTHAN_STATE_CD = 9
RAJASTHAN_COURT_CODES = {"jodhpur": 1, "jaipur": 2}
# CNRs start with the establishment: RJHC01 Jodhpur, RJHC02 Jaipur.
_CNR_BENCH = {"RJHC01": "jodhpur", "RJHC02": "jaipur"}

SCI_CASE_NO = "https://www.sci.gov.in/case-status-case-no/"
SCI_DIARY_NO = "https://www.sci.gov.in/case-status-diary-no/"
# e-SCR has no public URL built from a citation (its judgment links carry an internal id), so we
# link its search and give the neutral citation to paste.
ESCR = "https://digiscr.sci.gov.in/"

_CNR_RE = re.compile(r"^[A-Z]{4}\d{12}$")


def _ecourts_page(page: str, bench: str) -> str:
    return (f"{ECOURTS_HC}/{page}?state_cd={RAJASTHAN_STATE_CD}&dist_cd=1"
            f"&court_code={RAJASTHAN_COURT_CODES[bench]}&stateNm=Rajasthan")


def _case_fields(j: dict) -> list[dict]:
    return [{"label": label, "value": str(j[key])}
            for label, key in (("Case type", "case_type"), ("Case number", "case_number"), ("Year", "case_year"))
            if j.get(key) is not None]


def status_links(j: dict) -> dict | None:
    """{"links": [{label, url}], "fields": [{label, value, copy?}], "note"}; the first link is the
    main one. None when there is nothing useful to link (an unknown court, or no case details)."""
    if j.get("court") == SUPREME_COURT:
        return _supreme(j)
    if j.get("court") == "Rajasthan High Court":
        return _rajasthan(j)
    return None


def _rajasthan(j: dict) -> dict | None:
    cnr = (j.get("cnr") or "").strip().upper()
    cnr = cnr if _CNR_RE.match(cnr) else ""
    bench = j.get("bench") if j.get("bench") in RAJASTHAN_COURT_CODES else _CNR_BENCH.get(cnr[:6])
    fields = _case_fields(j)
    if not cnr and not (bench and fields):
        return None
    links = []
    if cnr:
        links.append({"label": "Check case status", "url": ECOURTS_CNR_SEARCH})
    if bench:
        links.append({"label": "Search by case number" if cnr else "Check case status",
                      "url": _ecourts_page("cases/case_no.php", bench)})
    return {
        "links": links,
        "fields": ([{"label": "CNR", "value": cnr, "copy": True}] if cnr else [])
                  + ([{"label": "Bench", "value": bench.title()}] if bench else []) + fields,
        "note": ("On eCourts, paste the CNR and type the captcha." if cnr
                 else "On eCourts, pick the case type, enter the number and year, and type the captcha."),
    }


def _supreme(j: dict) -> dict | None:
    fields = _case_fields(j)
    if not fields and not j.get("neutral_citation"):
        return None
    # The diary number is not in the dataset, so the case number search comes first.
    links = [{"label": "Check case status", "url": SCI_CASE_NO}] if fields else []
    links.append({"label": "Search by diary number" if fields else "Check case status", "url": SCI_DIARY_NO})
    if j.get("neutral_citation"):
        links.append({"label": "Judgment on e-SCR", "url": ESCR})
        fields.append({"label": "Neutral citation", "value": j["neutral_citation"], "copy": True})
    return {
        "links": links,
        "fields": fields,
        "note": ("On the Supreme Court site, pick the case type, enter the number and year, and type the captcha."
                 if links[0]["url"] == SCI_CASE_NO else "On the Supreme Court site, enter the diary number and type the captcha."),
    }
