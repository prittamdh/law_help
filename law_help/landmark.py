"""Landmark judgments: the ones later judgments cite most.

`importer citations` (and so `structure` and `update`) rebuilds the `citations` table. The
Rajasthan HC's own orders are linked by case number in SQL (importer.LINK_CASES_SQL). This
module adds the links to Supreme Court judgments, which are cited by reporter instead:

* by neutral citation, "2024 INSC 735", or Supreme Court Reports citation,
  "(1990) 2 SCR 212" / "[1990] 2 S.C.R. 212", when the judgment has one;
* otherwise by name and year: "Gian Singh v. State of Punjab, (2012) 10 SCC 303" links to
  the one Supreme Court judgment between those parties decided in 2012 (or late 2011, as
  SCC often reports a year on). Case reporters we don't have (SCC, AIR) only give the year,
  so a name that fits two judgments in those years is left unlinked.

Then `cited_counts` holds how many different judgments cite each one. A batch of connected
cases decided by one common order counts once, as on the judgment page.

A landmark is a judgment in the most cited 1% of its court's cited judgments, and cited by
at least LANDMARK_MIN. The cut-off is relative because counts grow with the collection:
with the Supreme Court's own judgments only, the 1% line is 17 citations; the High Court's
million orders cite the Supreme Court far more.
"""

import logging
import re

import psycopg

from .extract import _case_key

log = logging.getLogger("law_help.landmark")

SC_SOURCE = "aws-sc-judgments"

LANDMARK_SHARE = 0.01  # the most cited 1% of each court's cited judgments
LANDMARK_MIN = 10      # and never fewer citations than this

_INSC_RE = re.compile(r"(\d{4})\s*INSC\s*(\d+)")
_SCR_RE = re.compile(r"[\[(](\d{4})[\])]\s*(\d+)\s*S\.?\s?C\.?\s?R\.?\s*(\d+)|"
                     r"(\d{4})\s*\((\d+)\)\s*S\.?\s?C\.?\s?R\.?\s*(\d+)")
# A reporter of Supreme Court judgments: SCC, SCR, AIR 1990 SC, JT 2005 (12) SC, SCALE, INSC, (SC)
_SC_REPORTER_RE = re.compile(r"\bS\.?\s?C\.?\s?[CR]\b|\bAIR\s*\d{4}\s*S\.?\s?C\b|\bJT\b|SCALE|INSC|\(SC\)")
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")


def insc_key(citation: str) -> str | None:
    m = _INSC_RE.search(citation)
    return f"{m[1]} INSC {int(m[2])}" if m else None


def scr_key(citation: str) -> str | None:
    m = _SCR_RE.search(citation)
    if not m:
        return None
    y, v, p = (m[1], m[2], m[3]) if m[1] else (m[4], m[5], m[6])
    return f"{y}/{int(v)}/{int(p)}"


class SupremeIndex:
    """Every Supreme Court judgment, looked up by citation or by parties and year."""

    def __init__(self, rows):
        self.by_cite: dict[str, int] = {}
        self.by_name: dict[str, list[tuple[int, int]]] = {}
        for r in rows:
            for key in (insc_key(r["neutral_citation"] or ""), scr_key(r["report_citation"] or "")):
                if key:
                    self.by_cite[key] = r["id"]
            if r["petitioner"] and r["respondent"] and r["decision_date"]:
                name = _case_key(f"{r['petitioner']} v. {r['respondent']}")
                self.by_name.setdefault(name, []).append((r["decision_date"].year, r["id"]))

    def find(self, cited: dict) -> int | None:
        cites = cited.get("citations") or []
        for c in cites:
            for key in (insc_key(c), scr_key(c)):
                if key and key in self.by_cite:
                    return self.by_cite[key]
        name = cited.get("name")
        years = {int(y) for c in cites if _SC_REPORTER_RE.search(c) for y in _YEAR_RE.findall(c)[:1]}
        if not name or not years:
            return None
        name = re.sub(r"\s+(?:vs?\.?|v/s|versus)\s+", " v. ", name, count=1, flags=re.I)
        found = {jid for year, jid in self.by_name.get(_case_key(name), ())
                 if any(year in (y, y - 1) for y in years)}
        return found.pop() if len(found) == 1 else None


def link_supreme(conn: psycopg.Connection) -> int:
    """Add citations to Supreme Court judgments; returns how many were added."""
    index = SupremeIndex(conn.execute(
        "SELECT id, neutral_citation, report_citation, petitioner, respondent, decision_date "
        "FROM judgments WHERE source = %s", (SC_SOURCE,)))
    if not index.by_cite and not index.by_name:
        return 0
    added = 0
    with conn.cursor(name="sc_links") as cur, conn.cursor() as w:
        cur.execute("SELECT id, cases_cited FROM judgments WHERE cases_cited IS NOT NULL AND cases_cited <> '[]'")
        while rows := cur.fetchmany(5000):
            links = {(r["id"], cited) for r in rows for c in r["cases_cited"]
                     if (cited := index.find(c)) is not None and cited != r["id"]}
            w.executemany("INSERT INTO citations (citing_id, cited_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                          sorted(links))
            added += len(links)
    return added


COUNT_SQL = """
INSERT INTO cited_counts (judgment_id, n)
SELECT c.cited_id, count(DISTINCT coalesce(a.neutral_citation, md5(a.full_text), a.id::text))
FROM citations c JOIN judgments a ON a.id = c.citing_id
GROUP BY 1
"""


THRESHOLD_SQL = """
INSERT INTO landmark_thresholds (court, n)
SELECT j.court, greatest(%(min)s, percentile_disc(%(q)s) WITHIN GROUP (ORDER BY c.n))
FROM cited_counts c JOIN judgments j ON j.id = c.judgment_id
GROUP BY 1
"""


def count_citations(conn: psycopg.Connection) -> None:
    conn.execute("DELETE FROM cited_counts")
    conn.execute(COUNT_SQL)
    conn.execute("DELETE FROM landmark_thresholds")
    conn.execute(THRESHOLD_SQL, {"min": LANDMARK_MIN, "q": 1 - LANDMARK_SHARE})


def landmark_sql(alias: str = "judgments") -> str:
    """A SQL condition: this judgment is cited often enough, for its court, to be a landmark."""
    return (f"coalesce((SELECT n FROM cited_counts WHERE judgment_id = {alias}.id), 0) >= "
            f"coalesce((SELECT n FROM landmark_thresholds WHERE court = {alias}.court), 2147483647)")
