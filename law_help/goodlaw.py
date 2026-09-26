"""Good law check: flag a judgment that a later judgment of this court set aside, recalled
or overruled.

Three rules, all over judgments already in the database:

- **Set aside on appeal.** A Division Bench hearing a special appeal (SAW) against a Single
  Judge says "the order dated 05.04.2023 passed by the learned Single Judge is set aside".
  The order is found by its date and, when the appeal names it, its case number; without a
  case number the parties must match too.
- **Recalled on review.** A review petition (WRW, CRW, CRLRW) that is allowed and says the
  order dated D "is recalled" or "set aside".
- **Overruled.** A later judgment says a judgment it cites by case number or neutral
  citation "is overruled" or "does not lay down the correct law".

A wrong flag tells a lawyer to stop relying on a judgment that is still good law, which
is worse than no flag, so every rule insists on the court's own operative words in one
sentence, skips sentences that report what counsel argued or asked for, and leaves a
judgment unflagged unless exactly one order fits.
"""

import re
from datetime import date

from . import db, extract

APPEAL_TYPES = ("SAW",)                        # D.B. Special Appeal (Writ) against a Single Judge
REVIEW_TYPES = ("WRW", "CRW", "CRLRW")         # writ, civil and criminal review petitions

KINDS = {
    "set_aside": "Set aside on appeal",
    "partly_set_aside": "Partly set aside on appeal",
    "recalled": "Recalled on review",
    "overruled": "Overruled",
}

# --------------------------------------------------------------------------- dates

_MONTHS = {m: i for i, m in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
_DATE_RE = re.compile(
    r"\b(\d{1,2})\s?[./-]\s?(\d{1,2})\s?[./-]\s?((?:19|20)\d{2})\b"
    r"|\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([A-Z][a-z]{2,8})\.?,?\s+((?:19|20)\d{2})\b")


def dates(text: str) -> list[date]:
    """Dates written as 05.04.2023, 5/4/2023, 05-04-2023 or 5th April, 2023, in order."""
    out = []
    for m in _DATE_RE.finditer(text):
        if m.group(1):
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            mo = _MONTHS.get(m.group(5)[:3].lower())
            if not mo:
                continue
            d, y = int(m.group(4)), int(m.group(6))
        try:
            out.append(date(y, mo, d))
        except ValueError:
            continue
    return out


# Like extract._CASE_NO_RE, but also "SB Civil Writ Petition No.7396 of 2023".
_CASE_NO_RE = re.compile(
    r"\b([SDFL])\.?\s?B\.?\s*([A-Za-z][A-Za-z .()/&-]{2,80}?)\s*No\.?\s*:?\s*(\d{1,6})"
    r"\s*(?:/|\s+of\s+)\s*((?:19|20)\d{2})\b")
# Any case number at all, to know when one was named but not understood.
_ANY_CASE_NO_RE = re.compile(r"\bNo\.?\s*:?\s*\d{1,6}\s*(?:/|\s+of\s+)\s*(?:19|20)\d{2}\b", re.I)


def case_numbers(text: str) -> list[str]:
    """This court's case numbers in `text` as "TYPE/NO/YEAR" keys ("CRLAS|CRLA/12/2020")."""
    out = []
    for m in _CASE_NO_RE.finditer(text):
        kind = extract.case_kind(m.group(2), m.group(1))
        if kind:
            key = f"{kind}/{int(m.group(3))}/{m.group(4)}"
            if key not in out:
                out.append(key)
    return out


# --------------------------------------------------------------------------- sentences

# The court's own operative words, in the present: "is hereby quashed and set aside",
# "stands set aside", "we set aside", "are inclined to set aside", "is recalled".
_SET_ASIDE = r"(?:quashed\s*(?:and|&)\s*)?set[\s-]*aside"
_OPERATIVE_RE = re.compile(
    rf"\b(?:is|are|stands?|be)\s+(?:hereby\s+|accordingly\s+|therefore\s+)?{_SET_ASIDE}"
    rf"|\bwe\s*,?\s+(?:[\w,]+\s+){{0,5}}?{_SET_ASIDE}"
    rf"|\b(?:is|are|stands?|be)\s+(?:hereby\s+|accordingly\s+|therefore\s+)?(?:quashed|reversed)\b",
    re.I)
_RECALL_RE = re.compile(
    r"\b(?:is|are|stands?|be)\s+(?:hereby\s+|accordingly\s+|therefore\s+)?(?:recalled|reviewed)\b"
    r"|\bwe\s*,?\s+(?:[\w,]+\s+){0,5}?recall\b", re.I)
_OVERRULE_RE = re.compile(
    r"\b(?:is|are|stands?|be)\s+(?:hereby\s+|accordingly\s+|therefore\s+)?overruled\b"
    r"|\bwe\s*,?\s+(?:[\w,]+\s+){0,5}?overrule\b"
    r"|\bdo(?:es)?\s+not\s+lay\s+down\s+(?:the\s+|a\s+)?(?:correct|good)\s+law\b"
    r"|\b(?:is|are)\s+not\s+(?:a\s+)?good\s+law\b", re.I)

# Sentences that report what someone argued or asked for, or say what might happen.
_NOT_THE_COURT_RE = re.compile(
    r"\b(?:not|no(?!\.?\s*\d)|cannot|can't|neither|nor|unless|if|whether|liberty|in case|"
    r"pray\w*|seek\w*|sought|urge\w*|contend\w*|contention|submit\w*|submission|argu\w*|"
    r"request\w*|plea|claim\w*|according to|would|could|might)\b", re.I)
# The part of an order a Division Bench sets aside when it only partly allows the appeal.
_PARTLY_RE = re.compile(r"\b(?:to the extent|in so far|insofar|so far as|qua|partly|partially|"
                        r"modified|except|paragraphs?|paras?|directions?|observations?|findings?|"
                        r"costs?|penalty|remarks?)\b", re.I)
# The appeal refers to the order under appeal without its date.
_IMPUGNED_RE = re.compile(
    r"\bimpugned\s+(?:order|judgment)|\b(?:order|judgment)\s+(?:passed\s+)?(?:by|of)\s+(?:the\s+)?"
    r"learned\s+Single\s+Judge|\b(?:order|judgment)\s+of\s+(?:the\s+)?writ\s+court|"
    r"\b(?:order|judgment)\s+under\s+(?:appeal|review)", re.I)
# "order dated 05.04.2023": the date of an order, not of a notice or an event.
_ORDER_DATED_RE = re.compile(r"\b(?:order|judgment)s?\s+dated\s*:?\s*", re.I)
# How an appeal or review describes what it is against: "The appeal is directed against the
# order dated 05.04.2023 passed in S.B. Civil Writ Petition No.15358/2022".
_AGAINST_RE = re.compile(
    r"\b(?:directed|preferred|filed|against|assail\w*|challeng\w*|aggrieved|questioned|"
    r"impugned|arises?|arising|review\s+of|seeks?\s+review|recall\s+of)\b", re.I)
_OWN_COURT_RE = re.compile(r"\b(?:Single\s+Judge|Single\s+Bench|writ\s+court|this\s+Court|"
                           r"writ\s+petition|[SD]\.\s?B\.)", re.I)


def _operative(sentence: str, rx: re.Pattern) -> bool:
    m = rx.search(sentence)
    if not m:
        return False
    # "does not lay down the correct law" is itself the court's word; "no ground to set aside" is not.
    words = sentence[:m.start()] + " " + re.sub(r"\bnot\s+(?=lay|a\s+good|good)", "", m.group())
    return not _NOT_THE_COURT_RE.search(words)


def orders_set_aside(body_text: str, review: bool = False) -> list[dict]:
    """What an appeal (or, with `review`, a review petition) sets aside, from its closing lines:

        [{"kind": "set_aside", "dates": [date(2023, 4, 5)], "cases": ["CW/15358/2022"],
          "quote": "We accordingly set aside the order dated 05.04.2023 ..."}]

    `dates` are the dates of the order set aside; `cases` its case numbers when named.
    """
    sents = extract._sentences(body_text)
    if not sents:
        return []
    # The order under appeal, as the judgment describes it near the start.
    impugned_dates, impugned_cases = [], []
    for s in sents[:12]:
        if _AGAINST_RE.search(s) and _OWN_COURT_RE.search(s):
            first = next((dates(s[m.end():m.end() + 30]) for m in _ORDER_DATED_RE.finditer(s)
                          if dates(s[m.end():m.end() + 30])), None)
            if first:
                impugned_dates, impugned_cases = first[:1], case_numbers(s)
                break
    rx = _RECALL_RE if review else _OPERATIVE_RE
    out = []
    for s in sents[-10:]:
        if not (_operative(s, rx) or (review and _operative(s, _OPERATIVE_RE))):
            continue
        ds, cs = dates(s), case_numbers(s)
        if not ds and _IMPUGNED_RE.search(s) and len(impugned_dates) == 1:
            ds, cs = impugned_dates, cs or impugned_cases
        elif ds and not cs:
            cs = [c for c in impugned_cases] if set(ds) <= set(impugned_dates) else []
        if not ds:
            continue
        m = rx.search(s) or _OPERATIVE_RE.search(s)
        kind = "recalled" if review else ("partly_set_aside" if _PARTLY_RE.search(_clause(s, m)) else "set_aside")
        # A case number the rules can't read means the parties can't stand in for it.
        named = bool(cs) or bool(_ANY_CASE_NO_RE.search(s))
        out.append({"kind": kind, "dates": ds, "cases": cs, "named": named, "quote": s})
    return out


def _clause(sentence: str, m: re.Match) -> str:
    """The words of `sentence` naming what the court set aside: "paragraph 11 of the order
    dated ... is set aside" before the verb, "we set aside the order ... qua ..." after it."""
    if sentence[m.start():m.start() + 2].lower() == "we":
        return re.split(r"\band\b|;", sentence[m.end():])[0]
    return re.split(r"\band\b|;|:", sentence[:m.end()])[-1]


def overrulings(body_text: str) -> list[dict]:
    """Judgments of this court that this one overrules, by case number or neutral citation:

        [{"kind": "overruled", "cases": ["CW/3222/2001"], "neutral": [], "quote": "..."}]
    """
    out = []
    for s in extract._sentences(body_text):
        if not _operative(s, _OVERRULE_RE):
            continue
        cases = case_numbers(s)
        neutral = re.findall(r"\b(\d{4}:RJ-(?:JP|JD):\d+)", s)
        if cases or neutral:
            out.append({"kind": "overruled", "cases": cases, "neutral": neutral, "quote": s})
    return out


# --------------------------------------------------------------------------- linking

# Judgments worth reading: appeals and reviews, and any that use the words of overruling.
CANDIDATES_SQL = f"""
SELECT id, case_type, bench, decision_date, title, petitioner, respondent, full_text, outcome
FROM judgments
WHERE full_text IS NOT NULL AND text_language = 'en'
  AND (case_type IN ({", ".join(f"'{t}'" for t in APPEAL_TYPES + REVIEW_TYPES)})
       OR search @@ to_tsquery('english', 'overrule | correct <-> law | good <-> law'))
"""

# The order an appeal or review set aside: decided that day, at the same seat, and not the
# appeal's own case. It is the case the judgment names or, with none named, a writ petition
# whose parties `_same_party` matches (for an appeal, a Single Judge's).
MATCH_SQL = """
SELECT t.id, t.case_type, t.case_number, t.case_year, t.bench_strength, t.petitioner, t.respondent,
       md5(coalesce(t.full_text, t.id::text)) AS text_key
FROM judgments t JOIN judgments j ON j.id = %(by)s
WHERE t.decision_date = %(date)s AND t.decision_date < j.decision_date
  AND t.bench = j.bench AND t.id <> j.id
  AND (t.case_type, t.case_number, t.case_year) IS DISTINCT FROM (j.case_type, j.case_number, j.case_year)
"""

# An overruled judgment must be one the overruling judgment cites ("cited by" link).
OVERRULED_SQL = """
SELECT t.id FROM citations c JOIN judgments t ON t.id = c.cited_id
WHERE c.citing_id = %(by)s
  AND (t.case_type || '/' || t.case_number || '/' || t.case_year = ANY(%(cases)s)
       OR split_part(t.neutral_citation, '-DB', 1) = ANY(%(neutral)s)
       OR split_part(t.neutral_citation, '-FB', 1) = ANY(%(neutral)s))
"""

INSERT_SQL = """
INSERT INTO treatments (judgment_id, by_id, kind, quote) VALUES (%s, %s, %s, %s)
ON CONFLICT (judgment_id, by_id) DO NOTHING
"""


def _variants(keys: list[str]) -> list[str]:
    return [v for k in keys for v in extract._ref_variants(k)]


WRIT_TYPES = {"CW", "WMAP", "CRLW"}
# Words that name no one in particular in an institution's name.
_WEAK_WORDS = set("""
    society samiti sanstha trust association school college university bank nagar nigam parishad
    panchayat gram committee department authority development board commission services service
    public health education medical district home new old road town city through
""".split())
# "Mukesh Kumar S/o Shri Bardi Lal": the name, then a parent's or husband's.
_RELATION_RE = re.compile(r"\b(?:[sdwc]\s?/\s?o|son|daughter|wife|widow|husband|care)\b", re.I)


def party_names(party: str | None) -> tuple[list[str], list[str]]:
    """The distinctive words of a party's own name and of the relative it is known by:

        "SMT. SONAL SHARMA C/O SHRI DINESH KUMAR SHARMA" -> (["sonal", "sharma"], ["dinesh", "sharma"])
    """
    own, _, relative = _RELATION_RE.sub("\0", (party or "").lower(), 1).partition("\0")
    words = lambda x: list(dict.fromkeys(
        w for w in re.findall(r"[a-z]{3,}", re.split(r"[,(]", x)[0])
        if w not in extract._COMMON_WORDS and w not in _WEAK_WORDS))
    return words(own), words(relative)


def _same_party(a: dict, b: dict) -> bool:
    """Whether judgments a and b name the same private party: the same first name, and a
    and a second name or the relative's first name in common. "Kamal Kishore Sankhla" is "Kamal Kishor
    Sankhla"; "Karan Singh S/o Shimbhu Dayal" is "Karan Singh Son Of Shri Shimbhu"; but
    "Jai Kishan Meena" is not "Lokesh Kumar Meena S/o Hari Kishan Meena"."""
    for x in (a["petitioner"], a["respondent"]):
        xs, xr = party_names(x)
        for y in (b["petitioner"], b["respondent"]):
            ys, yr = party_names(y)
            if xs and ys and xs[0] == ys[0] and (len(set(xs) & set(ys)) >= 2 or (xr and yr and xr[0] == yr[0])):
                return True
    return False


def treatments_for(conn, row: dict) -> list[tuple[int, int, str, str]]:
    """(judgment_id, by_id, kind, quote) for one candidate judgment."""
    body = extract.body(row["full_text"]) or row["full_text"]
    out = []
    appeal = row["case_type"] in APPEAL_TYPES
    review = row["case_type"] in REVIEW_TYPES
    if (appeal or review) and not re.match(r"(?i)dismiss|withdrawn|rejected", row["outcome"] or ""):
        for t in orders_set_aside(body, review=review):
            for d in t["dates"]:
                same_day = conn.execute(MATCH_SQL, {"by": row["id"], "date": d}).fetchall()
                if t["cases"]:
                    cases = set(_variants(t["cases"]))
                    found = [f for f in same_day
                             if f"{f['case_type']}/{f['case_number']}/{f['case_year']}" in cases]
                elif t["named"]:
                    found = []
                else:
                    found = [f for f in same_day if f["case_type"] in WRIT_TYPES and _same_party(row, f)
                             and (f["bench_strength"] == "single" or not appeal)]
                    # one order, or one common order deciding a batch of connected cases
                    if len({f["text_key"] for f in found}) > 1:
                        found = []
                out += [(f["id"], row["id"], t["kind"], t["quote"]) for f in found]
    for t in overrulings(body):
        found = conn.execute(OVERRULED_SQL, {
            "by": row["id"], "cases": _variants(t["cases"]), "neutral": t["neutral"]}).fetchall()
        out += [(f["id"], row["id"], t["kind"], t["quote"]) for f in found]
    # One flag per order: a full setting aside says more than a partial one.
    best: dict[int, tuple] = {}
    for t in out:
        if t[0] not in best or _RANK[t[2]] < _RANK[best[t[0]][2]]:
            best[t[0]] = t
    return list(best.values())


_RANK = {k: i for i, k in enumerate(["set_aside", "recalled", "overruled", "partly_set_aside"])}


def link_treatments(conn=None) -> int:
    """Rebuild the treatments table from every appeal, review and overruling; returns its size."""
    if conn is None:
        with db.connect() as conn:
            return link_treatments(conn)
    db.init_schema(conn)
    with conn.transaction():
        conn.execute("DELETE FROM treatments")
        with conn.cursor(name="goodlaw_candidates") as cur:
            cur.itersize = 500
            cur.execute(CANDIDATES_SQL)
            with conn.cursor() as w:
                for row in cur:
                    found = treatments_for(w, row)
                    if found:
                        w.executemany(INSERT_SQL, found)
    return conn.execute("SELECT count(*) AS n FROM treatments").fetchone()["n"]
