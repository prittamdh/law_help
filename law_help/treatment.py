"""How a later judgment treats a judgment it cites: followed, distinguished, doubted or just cited.

`importer citations` (and so `structure` and `update`) links each judgment to the ones it
cites; then `label_citations` reads the citing judgment's text around every place it cites
each one and labels the link in the `citations` table:

- **followed**: "relied upon", "followed", "squarely covered by", "in view of the law laid
  down in", "on all fours", "binding on this Court".
- **distinguished**: "distinguishable", "distinguished", "not applicable to the facts",
  "has no application", "of no help", "facts are different".
- **doubted**: "doubted", "per incuriam", "not good law", "not followed", "unable to agree".
- **cited**: none of these, the default.

The place a judgment is cited is its case number, its neutral or S.C.R. citation, its
parties' names ("Gian Singh v. State of Punjab"), or a later "Gian Singh (supra)". The
words looked at are that sentence (the clause holding the citation first, when it has
a label of its own) and the next sentence when it cites nothing else.

Like the good law check (law_help.goodlaw), it is rules only and errs towards "cited":
what counsel argued ("it was submitted that X is distinguishable") and what counsel relied
on are skipped, and a negation turns a phrase round ("not followed" is doubted, "cannot
be distinguished" is followed, "not squarely covered" is distinguished). When the
judgment says more than one thing about a case, doubted beats distinguished beats followed.
"""

import bisect
import logging
import os
import re

from . import extract, goodlaw, landmark

log = logging.getLogger("law_help.treatment")

LABELS = ("followed", "distinguished", "doubted", "cited")
_RANK = {"doubted": 0, "distinguished": 1, "followed": 2}

# --------------------------------------------------------------------------- phrases

_W = r"[\w’']+"
# (label, phrase, label when negated: None drops the phrase)
_RULES = [(label, re.compile(rx, re.I), negated) for label, rx, negated in [
    ("doubted", r"\bper\s+incuriam\b", None),
    ("doubted", r"\bnot\s+(?:a\s+|the\s+)?(?:good|correct)\s+law\b|\bno\s+longer\s+(?:good\s+law|holds?\s+(?:good|the\s+field))\b", None),
    ("doubted", r"\bdo(?:es)?\s+not\s+lay\s+down\s+(?:the\s+|a\s+)?(?:correct|good)\s+law\b", None),
    ("doubted", r"\b(?:wrongly|not\s+correctly|not\s+rightly)\s+decided\b", None),
    ("doubted", r"\bdoubt(?:ed|ing)\b|\bdoubts?\s+(?:about|on|as\s+to)\s+the\s+correctness\b", None),
    ("doubted", r"\b(?:unable|decline[sd]?|refuse[sd]?|hesitate)\s+to\s+(?:follow|agree|accept|concur)\b"
                r"|\bnot\s+(?:inclined|persuaded)\s+to\s+(?:follow|agree|accept)\b", None),
    ("doubted", r"\b(?:is|are|was|were|been|stands?)\s+overruled\b", None),
    ("distinguished", r"\bdistinguish(?:able|ed)\b|\bdistinguishing\s+features?\b", "followed"),
    ("distinguished", r"\bnot\s+(?:at\s+all\s+|strictly\s+|squarely\s+)?(?:applicable|apply|attracted|relevant|"
                      r"helpful|of\s+any\s+(?:help|assistance|avail))\b", None),
    ("distinguished", r"\b(?:has|have|had)\s+no\s+(?:application|applicability|relevance|bearing)\b"
                      r"|\bof\s+no\s+(?:help|assistance|avail|use)\b|\bdoes\s+not\s+(?:help|come\s+to\s+the\s+(?:aid|rescue))\b", None),
    ("distinguished", r"\bfacts\s+(?:\S+\s+){0,6}?(?:are|were|is|was)\s+(?:entirely\s+|quite\s+|totally\s+|clearly\s+|altogether\s+)?"
                      r"(?:different|distinct)\b|\b(?:different|distinct)\s+(?:set\s+of\s+)?facts\b"
                      r"|\bon\s+(?:its|their)\s+own\s+facts\b|\bdifferent\s+footing\b", "followed"),
    ("followed", r"\bsquarely\s+(?:covered|covers|cover|applies|apply|applicable|answered)\b|\bfully\s+covered\b"
                 r"|\bcovered\s+by\s+(?:the\s+)?(?:judgment|decision|ratio|view|order|dictum|law)\b|\bon\s+all\s+fours\b", "distinguished"),
    ("followed", r"\b(?:is|are|was|were|been|be|being)\s+(?:(?:not|never)\s+)?(?:respectfully\s+)?followed\b(?!\s+by\s+(?:a|an)\b)"
                 r"|\bwe\s+(?:would\s+|shall\s+|must\s+|may\s+)?(?:respectfully\s+)?follow\b"
                 r"|\bfollow(?:s|ing|ed)?\s+the\s+(?:ratio|law|view|dictum|dicta|principles?|decision|judgment|precedent)\b", "doubted"),
    ("followed", r"\bin\s+(?:the\s+)?(?:view|light)\s+of\s+the\s+(?:law|ratio|principles?|dictum|dicta|judgment|decision|view)s?\b"
                 r"|\bguided\s+by\b|\bfortified\s+by\b|\bbinding\s+(?:on|upon)\s+(?:this\s+court|us)\b"
                 r"|\b(?:respectfully\s+)?(?:agree|concur)\s+with\s+the\s+(?:view|ratio|reasoning|law|decision|judgment)\b"
                 r"|\bappl(?:y|ies)\s+(?:with\s+full\s+force\s+)?(?:to|in)\s+the\s+(?:present|instant|facts)\b"
                 r"|\bapplicable\s+(?:with\s+full\s+force\s+)?(?:to|in)\s+the\s+(?:present|instant|facts)\b", "distinguished"),
    ("relied", r"\brel(?:ied|ies|y|ying)\s+(?:up)?on\b|\breliance\s+(?:is|being|has\s+been)\s+placed\b", None),
]]

# "not", "cannot be", "is not squarely" just before a phrase.
_NEG_BEFORE_RE = re.compile(rf"(?:\b(?:not|never|no|cannot|hardly|neither|nor)\b|n[’']t)(?:\s+{_W}){{0,2}}\s*$", re.I)
_NEG_IN_RE = re.compile(r"\b(?:not|never)\b", re.I)
# What someone argued or asked for, or a question the court has yet to answer.
_ARGUED_RE = re.compile(
    r"\b(?:submit\w*|contend\w*|contention|argu\w*|urge[sd]?|urging|plead\w*|canvass\w*|pray\w*|"
    r"according\s+to|sought|seeks?|tried\s+to|attempt\w*|whether|if)\b", re.I)
# Reliance by a party, or by the court in the cited judgment ("In X, the Apex Court relied on Y").
_PARTY_RE = re.compile(
    r"\b(?:counsel|advocate|petitioners?|appellants?|respondents?|applicants?|accused|complainant|"
    r"prosecution|defen[cs]e|plaintiffs?|defendants?|revisionists?|parties|party|side|"
    r"A\.?A\.?G|P\.?P|G\.?A|Mr|Ms|Mrs|Shri|Sh)\b|\b(?:Supreme|Apex|Hon[’']?ble|Division|Full|Larger|"
    r"Co-?ordinate|Single)\s+(?:\w+\s+)?(?:Court|Bench|Judge)\b", re.I)
# "The preliminary objection is overruled" is not about a judgment.
_OBJECTION_RE = re.compile(r"\bobjections?\b", re.I)
# Where a sentence turns from one case to another.
_CLAUSE_RE = re.compile(r";|\bwhereas\b|\bwhile\b|\bbut\b|\bhowever\b", re.I)


def classify(sentence: str) -> str | None:
    """The treatment a sentence gives the judgment it cites, or None when it says nothing:

        classify("The judgment in Ram (supra) is clearly distinguishable on facts.") -> "distinguished"
        classify("The said judgment cannot be followed as it was rendered per incuriam.") -> "doubted"
    """
    found = set()
    for label, rx, negated in _RULES:
        for m in rx.finditer(sentence):
            before = sentence[:m.start()]
            if _ARGUED_RE.search(before) or (m.group().endswith("overruled") and _OBJECTION_RE.search(sentence)):
                continue
            if label == "relied":
                if _PARTY_RE.search(sentence) or _NEG_BEFORE_RE.search(before):
                    continue
                label_ = "followed"
            elif _NEG_BEFORE_RE.search(before[-60:]) or (negated and _NEG_IN_RE.search(m.group())):
                label_ = negated
            else:
                label_ = label
            if label_:
                found.add(label_)
    return min(found, key=_RANK.get) if found else None


# --------------------------------------------------------------------------- where a case is cited

def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", extract._flatten(text))


def _sentence_starts(flat: str) -> list[int]:
    """Where each sentence of `flat` starts (as extract._sentences splits, but a citation's
    page number, "10 SCC 303.", may end a sentence)."""
    starts = [0]
    for m in re.finditer(r"[.?!]\s+(?=(?:\d+\.\s+)?[A-Z“\"(])", flat):
        prev = flat[starts[-1]:m.start()].rsplit(" ", 1)[-1].lower().strip("(“\"")
        if prev in extract._ABBREV or re.fullmatch(r"[a-z]|[a-z](?:\.[a-z])+|\d{1,2}", prev):
            continue
        starts.append(m.end())
    return starts


_NC_RE = re.compile(r"\d{4}:RJ-(?:JP|JD):\d+")


class Mentions:
    """Every place in one judgment's text that cites some judgment, by what it cites with."""

    def __init__(self, text: str):
        self.flat = flat = _flat(text)
        self.starts = _sentence_starts(flat)
        self.by_key: dict[str, list[int]] = {}
        for m in goodlaw._CASE_NO_RE.finditer(flat):
            kind = extract.case_kind(m.group(2), m.group(1))
            if kind:
                for v in extract._ref_variants(f"{kind}/{int(m.group(3))}/{m.group(4)}"):
                    self.by_key.setdefault(v, []).append(m.start())
        for m in _NC_RE.finditer(flat):
            self.by_key.setdefault(f"NC:{m.group()}", []).append(m.start())
        for rx, key in ((landmark._INSC_RE, landmark.insc_key), (landmark._SCR_RE, landmark.scr_key)):
            for m in rx.finditer(flat):
                self.by_key.setdefault(f"SC:{key(m.group())}", []).append(m.start())
        self.names: list[tuple[int, str, str]] = []   # (position, first party's key, second's first word)
        for m in extract._CASE_NAME_RE.finditer(flat):
            a, b = extract._tidy_name(m.group("a"), lead=True), extract._tidy_name(m.group("b"), lead=False)
            if len(a) >= 3 and len(b) >= 3:
                ka, _, kb = extract._case_key(f"{a} v. {b}").partition("|")
                self.names.append((m.start() + max(m.group().find(a), 0), ka, kb.split(" ")[0]))
        # Anything that cites some case, to keep the next sentence to the one case.
        self.any = sorted({p for ps in self.by_key.values() for p in ps} | {p for p, _, _ in self.names}
                          | {m.start() for m in extract.CITATION_RE.finditer(flat)}
                          | {m.start() for m in re.finditer(r"\bsupra\b", flat, re.I)})

    def positions(self, cited: dict) -> list[int]:
        """Where the judgment `cited` (a judgments row) is cited."""
        out = []
        if cited.get("case_type") and cited.get("case_number") is not None and " " not in cited["case_type"]:
            out += self.by_key.get(f"{cited['case_type']}/{cited['case_number']}/{cited['case_year']}", [])
        nc = cited.get("neutral_citation") or ""
        if m := _NC_RE.search(nc):
            out += self.by_key.get(f"NC:{m.group()}", [])
        for key in (landmark.insc_key(nc), landmark.scr_key(cited.get("report_citation") or "")):
            if key:
                out += self.by_key.get(f"SC:{key}", [])
        pet, res = _parties(cited)
        if pet and res:
            ka, _, kb = extract._case_key(f"{pet} v. {res}").partition("|")
            out += [p for p, a, b in self.names if ka and a == ka and b == kb.split(" ")[0]]
        # "Kheta Ram (supra)", by the first distinctive word of either party's name
        word = next((w for p in (pet, res) for w in goodlaw.party_names(p)[0] if len(w) >= 4), None)
        if word:
            out += [m.start() for m in re.finditer(rf"\b{re.escape(word)}\b(?=[^;]{{0,80}}?\bsupra\b)", self.flat, re.I)]
        return sorted(set(out))

    def sentence(self, pos: int) -> tuple[int, int]:
        i = bisect.bisect_right(self.starts, pos) - 1
        return self.starts[i], self.starts[i + 1] if i + 1 < len(self.starts) else len(self.flat)

    def treatment(self, cited: dict) -> tuple[str, str | None]:
        """(label, the sentence that gave it) for the judgment `cited`."""
        best: tuple[str, str] | None = None
        for pos in self.positions(cited):
            start, end = self.sentence(pos)
            s = self.flat[start:end]
            # the clause holding the citation, when the sentence turns to another case
            cuts = [0] + [m.end() for m in _CLAUSE_RE.finditer(s)] + [len(s)]
            i = bisect.bisect_right(cuts, pos - start) - 1
            found = classify(s[cuts[i]:cuts[i + 1]]) or classify(s)
            quote = s
            if not found and end < len(self.flat):
                nstart, nend = self.sentence(end)
                j = bisect.bisect_left(self.any, nstart)
                if j == len(self.any) or self.any[j] >= nend:     # the next sentence cites nothing else
                    found = classify(self.flat[nstart:nend])
                    quote = f"{s.strip()} {self.flat[nstart:nend].strip()}"
            if found and (best is None or _RANK[found] < _RANK[best[0]]):
                best = (found, quote.strip())
        if best is None:
            return "cited", None
        label, quote = best
        quote = re.sub(r"^\d+\.\s*", "", quote)
        return label, quote if len(quote) <= 600 else quote[:597] + "…"


def _parties(cited: dict) -> tuple[str | None, str | None]:
    if cited.get("petitioner") and cited.get("respondent"):
        return cited["petitioner"], cited["respondent"]
    # "CW/6863/2014 of KHETA RAM Vs STATE"
    title = re.sub(r"^\S+ of ", "", cited.get("title") or "")
    parts = re.split(r"\s+(?:vs?\.?|versus)\s+", title, maxsplit=1, flags=re.I)
    return (parts[0], parts[1]) if len(parts) == 2 else (None, None)


def treatments(text: str, cited: list[dict]) -> dict[int, tuple[str, str | None]]:
    """{cited judgment id: (label, quote)} for one citing judgment's text."""
    m = Mentions(extract.body(text) or text)
    return {c["id"]: m.treatment(c) for c in cited}


# --------------------------------------------------------------------------- labelling links

# Each citing judgment with its text and the judgments it cites.
CITING_SQL = """
SELECT j.id, j.full_text,
       json_agg(json_build_object('id', t.id, 'case_type', t.case_type, 'case_number', t.case_number,
                'case_year', t.case_year, 'neutral_citation', t.neutral_citation,
                'report_citation', t.report_citation, 'petitioner', t.petitioner,
                'respondent', t.respondent, 'title', t.title)) AS cited
FROM citations c JOIN judgments j ON j.id = c.citing_id JOIN judgments t ON t.id = c.cited_id
WHERE j.full_text IS NOT NULL
GROUP BY j.id
"""

UPDATE_SQL = """
UPDATE citations SET treatment = %s, treatment_quote = %s WHERE citing_id = %s AND cited_id = %s
"""


def _label_many(rows: list[tuple[int, str, list[dict]]]) -> list[tuple[str, str | None, int, int]]:
    out = []
    for citing, text, cited in rows:
        try:
            found = treatments(text, cited)
        except Exception:  # one odd text must not stop the rest
            log.exception("could not label the citations of judgment %s", citing)
            continue
        out += [(label, quote, citing, cid) for cid, (label, quote) in found.items() if label != "cited"]
    return out


def label_citations(conn, workers: int | None = None) -> int:
    """Label every link in `citations` from the citing judgment's text; returns how many got a
    label other than "cited". Runs inside the caller's transaction (see importer.link_citations)."""
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    workers = (os.cpu_count() or 1) if workers is None else workers
    conn.execute("UPDATE citations SET treatment = 'cited', treatment_quote = NULL "
                 "WHERE treatment <> 'cited' OR treatment_quote IS NOT NULL")
    pool, done = None, 0
    try:
        with conn.cursor(name="treatment_citing") as cur, conn.cursor() as w:
            cur.execute(CITING_SQL)
            while rows := cur.fetchmany(workers * 50):
                items = [(r["id"], r["full_text"], r["cited"]) for r in rows]
                if pool is None and workers > 1 and len(rows) == workers * 50:
                    pool = ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("forkserver"))
                chunks = [items[i:i + 50] for i in range(0, len(items), 50)]
                labels = [x for c in (pool.map(_label_many, chunks) if pool else map(_label_many, chunks)) for x in c]
                if labels:
                    w.executemany(UPDATE_SQL, labels)
                done += len(labels)
    finally:
        if pool:
            pool.shutdown()
    return done
