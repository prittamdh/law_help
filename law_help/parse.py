"""Turn raw eCourts metadata fields into structured values."""

import re
from datetime import date, datetime

# "CW/16422/2024 of PETITIONER Vs RESPONDENT"; the case type is sometimes blank.
_TITLE_RE = re.compile(
    r"^\s*(?P<type>[^/]*)/(?P<number>\d+)/(?P<year>\d{4})\s+of\s+(?P<parties>.*)$",
    re.DOTALL,
)
_VS_RE = re.compile(r"\s+Vs\.?\s+", re.IGNORECASE)

BENCHES = {"jaipur": "jaipur", "rhcjodh240618": "jodhpur"}


def parse_title(title: str) -> dict:
    """Split a case title into case type/number/year and the two parties."""
    out = {"case_type": None, "case_number": None, "case_year": None,
           "petitioner": None, "respondent": None}
    m = _TITLE_RE.match(title or "")
    if not m:
        return out
    out["case_type"] = m["type"].strip() or None
    out["case_number"] = int(m["number"])
    out["case_year"] = int(m["year"])
    parties = _VS_RE.split(m["parties"], maxsplit=1)
    out["petitioner"] = _clean_party(parties[0])
    if len(parties) > 1:
        out["respondent"] = _clean_party(parties[1])
    return out


def _clean_party(s: str) -> str | None:
    s = s.strip().rstrip(",").strip()
    return s or None


def parse_judges(judge: str | None) -> list[str]:
    """Division and full benches list several judges separated by commas."""
    if not judge:
        return []
    return [j.strip() for j in judge.split(",") if j.strip()]


def bench_strength(judges: list[str]) -> str | None:
    return {0: None, 1: "single", 2: "division"}.get(len(judges), "full")


def parse_date(value) -> date | None:
    """Accept dd-mm-yyyy strings, datetimes and dates."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value).strip(), "%d-%m-%Y").date()
    except ValueError:
        return None
