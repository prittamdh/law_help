"""Similar judgments: GET /judgments/{id}/similar, shown on the judgment page.

No embeddings: a judgment is like another when they rest on the same authorities.
Signals, strongest first:

* cases in common: both cite the same judgment (the `citations` table), or the same
  reported case by the same citation string (`cases_cited`). A case that thousands of
  judgments cite says less than one cited twice, so each linked case counts
  1 / (1 + log10(times cited)).
* cited together: later judgments cite both (co-citation).
* acts and sections in common (`acts_cited`); CrPC s. 439 and other procedural provisions
  count half, as they say how a case reached court rather than what it is about.
* the same case type, and a small preference for recent judgments to break ties.

Every lookup is bounded, so a judgment citing Maneka Gandhi or IPC s. 302 costs the same
as one citing an obscure order: at most MAX_CITED cited cases, the newest PER_CITED
judgments citing each, the newest MAX_CITERS judgments citing this one, and PER_POOL
judgments per act-section or citation string (through the jsonb_path_ops GIN indexes; in
table order, since sorting them would read every match). The best SHORTLIST candidates are then scored exactly. Connected cases decided by one
common order are shown once, and other orders in this judgment's own case not at all.
"""

import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.types.json import Jsonb

from . import db
from .extract import _ACT_SHORT, _is_procedural

router = APIRouter()

MAX_CITED = 30     # this judgment's cited cases, least cited first
PER_CITED = 100    # judgments citing each of those, newest first
MAX_CITERS = 100   # judgments citing this one, for "cited together"
MAX_PAIRS = 5      # act-sections looked up, substantive before procedural
MAX_CITES = 10     # citation strings looked up
PER_POOL = 100     # judgments taken from each lookup
SHORTLIST = 200    # candidates scored exactly
LIMIT = 10

W_LINKED = 3.0     # per linked case in common, before the rarity discount
W_TEXT = 2.0       # per further case in common by citation string only
W_TOGETHER = 2.0   # per later judgment citing both
W_SECTION = 1.0    # per act-section in common (procedural: half)
W_CASE_TYPE = 1.0
W_RECENT = 0.2     # at most, for a judgment decided today

# The order a row belongs to: connected cases share a neutral citation or the whole text.
ORDER_KEY = "coalesce(neutral_citation, md5(full_text), id::text)"

THIS_SQL = f"""
SELECT id, court, case_type, case_number, case_year, acts_cited, cases_cited, {ORDER_KEY} AS order_key
FROM judgments WHERE id = %s
"""

# Judgments that cite the same judgments as this one (bibliographic coupling).
LINKED_SQL = """
SELECT o.citing_id AS id, count(*) AS n, sum(1.0 / (1 + log(m.n))) AS weight
FROM (
    SELECT c.cited_id, greatest(coalesce(cc.n, 1), 1) AS n
    FROM citations c LEFT JOIN cited_counts cc ON cc.judgment_id = c.cited_id
    WHERE c.citing_id = %(id)s
    ORDER BY n, c.cited_id
    LIMIT %(max_cited)s
) m
CROSS JOIN LATERAL (
    SELECT c.citing_id FROM citations c
    WHERE c.cited_id = m.cited_id AND c.citing_id <> %(id)s
    ORDER BY c.citing_id DESC
    LIMIT %(per_cited)s
) o
GROUP BY o.citing_id
"""

# Judgments cited alongside this one by the same later judgments (co-citation). A common
# order deciding connected cases is one citing judgment, not one per case.
TOGETHER_SQL = """
SELECT c.cited_id AS id, count(DISTINCT coalesce(a.neutral_citation, a.id::text)) AS n
FROM (
    SELECT citing_id FROM citations WHERE cited_id = %(id)s ORDER BY citing_id DESC LIMIT %(max_citers)s
) m
JOIN judgments a ON a.id = m.citing_id
JOIN citations c ON c.citing_id = m.citing_id
WHERE c.cited_id <> %(id)s
GROUP BY c.cited_id
"""

# Up to PER_POOL judgments whose `column` contains each probe, with the probes each matched.
# Each probe must be a bitmap scan of the GIN index, which stops after PER_POOL rows: run
# with enable_seqscan off, as the planner otherwise guesses that a sequential scan soon
# finds PER_POOL rows of a common probe, and reads the whole table when the guess is wrong.
# (Filtering on case type here is no better: it tempts the planner into walking
# judgments_case_idx through a quarter of the table for bail applications.)
POOL_SQL = """
SELECT k.id, k.case_type, array_agg(DISTINCT q.i) AS hits
FROM jsonb_to_recordset(%(probes)s) AS q(i int, probe jsonb)
CROSS JOIN LATERAL (
    SELECT id, case_type FROM judgments
    WHERE {column} @> q.probe AND id <> %(id)s
    LIMIT %(per_pool)s
) k
GROUP BY k.id, k.case_type
"""

SHORTLIST_SQL = """
SELECT id, court, case_type, case_number, case_year, decision_date, acts_cited, cases_cited
FROM judgments WHERE id = ANY(%s)
"""

RESULT_SQL = f"""
SELECT id, title, court, bench, case_type, case_number, case_year, decision_date, neutral_citation,
       {ORDER_KEY} AS order_key
FROM judgments WHERE id = ANY(%s)
"""


def _pairs(acts: list | None) -> list[tuple[str, str]]:
    """(act, section) pairs, what the case is about before how it reached court."""
    pairs = [(a["act"], s) for a in acts or [] for s in a.get("sections") or []]
    return sorted(dict.fromkeys(pairs), key=lambda p: _is_procedural(p[0]))


def _cites(cases: list | None) -> dict[str, int]:
    """Citation string -> index of the case it belongs to."""
    return {c: i for i, case in enumerate(cases or []) for c in case.get("citations") or []}


def _short(act: str) -> str:
    return _ACT_SHORT.get(act, re.sub(r",?\s*\d{4}$", "", act))


def reason(cases: int, together: int, sections: list[tuple[str, str]]) -> str:
    """One line on why: "3 cases in common · IPC 302, 34"."""
    parts = []
    if cases:
        parts.append(f"{cases} {'case' if cases == 1 else 'cases'} in common")
    if together:
        parts.append(f"cited together {together} {'time' if together == 1 else 'times'}")
    if sections:
        act = sections[0][0]
        secs = [s for a, s in sections if a == act]
        prefix = "art. " if act == "Constitution of India" else ""
        parts.append(f"{_short(act)} {prefix}{', '.join(secs[:3])}{'…' if len(secs) > 3 else ''}")
    return " · ".join(parts[:2])


def _recency(d: date | None) -> float:
    if not d:
        return 0.0
    return W_RECENT * max(0.0, min(1.0, (d.year - 1950) / (date.today().year - 1950 + 1)))


def _pool(conn, column: str, probes: list, params: dict) -> dict[int, dict]:
    if not probes:
        return {}
    return {r["id"]: r for r in conn.execute(POOL_SQL.format(column=column), {**params, "probes": Jsonb(probes)})}


def find_similar(conn, judgment_id: int, limit: int = LIMIT) -> list[dict] | None:
    """Up to `limit` judgments most like this one, best first, each with a `reason`.
    None when there is no such judgment."""
    this = conn.execute(THIS_SQL, (judgment_id,)).fetchone()
    if this is None:
        return None
    params = {"id": judgment_id, "max_cited": MAX_CITED,
              "per_cited": PER_CITED, "max_citers": MAX_CITERS, "per_pool": PER_POOL}

    linked = {r["id"]: r for r in conn.execute(LINKED_SQL, params)}
    together = {r["id"]: r["n"] for r in conn.execute(TOGETHER_SQL, params)}

    # Pools from the jsonb columns: each act-section, all of them together, each citation string.
    pairs = _pairs(this["acts_cited"])
    probes = [{"i": i, "probe": [{"act": a, "sections": [s]}]} for i, (a, s) in enumerate(pairs[:MAX_PAIRS])]
    if len(pairs) > 1:
        probes.append({"i": -1, "probe": this["acts_cited"]})
    cites = _cites(this["cases_cited"])
    seqscan = conn.execute("SELECT current_setting('enable_seqscan') AS v, "
                           "set_config('enable_seqscan', 'off', true)").fetchone()["v"]
    try:
        act_hits = _pool(conn, "acts_cited", probes, params)
        text_hits = _pool(conn, "cases_cited", [{"i": i, "probe": [{"citations": [c]}]}
                                                for c, i in list(cites.items())[:MAX_CITES]], params)
    finally:
        conn.execute("SELECT set_config('enable_seqscan', %s, true)", (seqscan,))

    # Rough scores to pick the shortlist; exact ones below.
    rough: dict[int, float] = {}
    for k, r in linked.items():
        rough[k] = rough.get(k, 0) + W_LINKED * float(r["weight"])
    for k, n in together.items():
        rough[k] = rough.get(k, 0) + W_TOGETHER * n
    for k, r in text_hits.items():
        rough[k] = rough.get(k, 0) + W_TEXT * len(r["hits"])
    for k, r in act_hits.items():
        rough[k] = (rough.get(k, 0) + W_SECTION * (len(pairs) if -1 in r["hits"] else len(r["hits"]))
                    + (W_CASE_TYPE if this["case_type"] and r["case_type"] == this["case_type"] else 0))
    rough.pop(judgment_id, None)
    shortlist = sorted(rough, key=lambda k: (-rough[k], -k))[:SHORTLIST]
    if not shortlist:
        return []

    mine = set(pairs)
    scored = []
    for r in conn.execute(SHORTLIST_SQL, (shortlist,)):
        if r["court"] == this["court"] and r["case_type"] and (r["case_type"], r["case_number"], r["case_year"]) == (
                this["case_type"], this["case_number"], this["case_year"]):
            continue  # another order in this same case
        link = linked.get(r["id"])
        n_linked = link["n"] if link else 0
        n_text = len({cites[c] for c in _cites(r["cases_cited"]) if c in cites})
        shared = [p for p in _pairs(r["acts_cited"]) if p in mine]
        n_together = together.get(r["id"], 0)
        score = (W_LINKED * (float(link["weight"]) if link else 0.0)
                 + W_TEXT * max(0, n_text - n_linked)
                 + W_TOGETHER * n_together
                 + sum(W_SECTION * (0.5 if _is_procedural(a) else 1.0) for a, _ in shared))
        if score <= 0:
            continue
        if this["case_type"] and r["case_type"] == this["case_type"]:
            score += W_CASE_TYPE
        score += _recency(r["decision_date"])
        scored.append((score, r["id"], reason(max(n_linked, n_text), n_together, shared)))
    scored.sort(key=lambda s: (-s[0], -s[1]))

    # Show a common order once, and never this judgment's own.
    top = scored[:limit * 4]
    rows = {r["id"]: r for r in conn.execute(RESULT_SQL, ([s[1] for s in top],))}
    seen = {this["order_key"]}
    results = []
    for score, k, why in top:
        r = rows[k]
        if r["order_key"] in seen:
            continue
        seen.add(r.pop("order_key"))
        results.append({**r, "reason": why, "score": round(score, 2)})
        if len(results) == limit:
            break
    return results


def get_conn():
    with db.connect() as conn:
        yield conn


@router.get("/judgments/{judgment_id}/similar")
def similar_judgments(judgment_id: int, limit: int = Query(LIMIT, ge=1, le=LIMIT), conn=Depends(get_conn)):
    """Judgments like this one: cases cited in common, cited together, same acts and sections."""
    results = find_similar(conn, judgment_id, limit)
    if results is None:
        raise HTTPException(404, "judgment not found")
    return {"results": results}
