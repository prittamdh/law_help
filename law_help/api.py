"""Search API over imported judgments.

Run with: uvicorn law_help.api:app --reload
"""

import re
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from psycopg.types.json import Jsonb

from . import bareacts, db
from .acts_api import router as acts_router
from .extract import CASE_KINDS, canonical_act, headline
from .importer import BUCKET_URL

app = FastAPI(title="law_help", description="Search Rajasthan High Court judgments")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(acts_router)


@app.get("/", include_in_schema=False)
def search_page():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/judgment", include_in_schema=False)
def judgment_page():
    return FileResponse(STATIC_DIR / "judgment.html")


@app.get("/saved", include_in_schema=False)
def saved_page():
    return FileResponse(STATIC_DIR / "saved.html")


LIST_COLUMNS = """
    id, bench, cnr, case_type, case_number, case_year, title, petitioner, respondent,
    judges, bench_judges, bench_strength, disposal_nature, decision_date, pdf_key,
    neutral_citation, summary, ai_summary, outcome, acts_cited
"""


def get_conn():
    with db.connect() as conn:
        yield conn


def normalize_section(section: str) -> str:
    """Match how law_help.extract stores sections: '120 - b' -> '120B', 'Sec. 3(1)(r)' -> '3(1)(r)',
    'order 7 rule 11' -> 'Order VII Rule 11'."""
    if m := re.match(r"\s*order\s*([ivxl]+|\d+)\s*,?\s*rule\s*(\w+)\s*$", section, re.I):
        order = m[1].upper() if not m[1].isdigit() else m[1]
        return bareacts.section_key(f"Order {order} Rule {m[2].upper()}")
    s = re.sub(r"^\s*(?:sections?|secs?\.?|s\.|u/s\.?)\s*", "", section, flags=re.I)
    s = re.sub(r"[\s-]+", "", s)
    return re.sub(r"^(\d+)([a-z])(?![a-z])", lambda m: m[1] + m[2].upper(), s)


def _present(row: dict) -> dict:
    row["pdf_url"] = f"{BUCKET_URL}/{row.pop('pdf_key')}"
    row["headline"] = headline(row["case_type"], row["acts_cited"], row["outcome"], row["disposal_nature"])
    return row


@app.get("/judgments")
def search_judgments(
    q: str | None = Query(None, description="full-text search (web-search syntax)"),
    judge: str | None = Query(None, description="exact judge name, e.g. SAMEER JAIN"),
    act: str | None = Query(None, description="act cited, e.g. IPC or Indian Penal Code, 1860"),
    section: str | None = Query(None, description="section of `act`, e.g. 302 or 120-B"),
    equivalent: bool = Query(True, description="also match the same section in the old or new code "
                             "(IPC 302 also finds BNS 103)"),
    case_type: str | None = Query(None, description="e.g. CW, CRLMB"),
    bench: str | None = Query(None, pattern="^(jaipur|jodhpur)$"),
    disposal: str | None = Query(None, description="e.g. ALLOWED, DISMISSED"),
    decided_from: date | None = None,
    decided_to: date | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    conn=Depends(get_conn),
):
    where, params = [], {}
    if q:
        where.append("search @@ websearch_to_tsquery('english', %(q)s)")
        params["q"] = q
    if judge:
        # eCourts metadata and the judgment PDF occasionally disagree on judges; match either.
        where.append("(%(judge)s = ANY(judges) OR %(judge)s = ANY(bench_judges))")
        params["judge"] = judge.strip().upper()
    if section and not act:
        raise HTTPException(422, "section needs an act")
    added = []
    if act and section:
        # The section as judgments cite it, and its counterpart in the old or new code (IPC 302 is BNS 103).
        forms, added = bareacts.search_forms(canonical_act(act), normalize_section(section), equivalent)
        where.append("(" + " OR ".join(f"acts_cited @> %(cited{i})s" for i in range(len(forms))) + ")")
        params.update({f"cited{i}": Jsonb([f]) for i, f in enumerate(forms)})
    elif act:
        where.append("acts_cited @> %(acts_cited)s")
        params["acts_cited"] = Jsonb([{"act": canonical_act(act)}])
    if case_type:
        where.append("case_type = %(case_type)s")
        params["case_type"] = case_type.upper()
    if bench:
        where.append("bench = %(bench)s")
        params["bench"] = bench
    if disposal:
        where.append("disposal_nature = %(disposal)s")
        params["disposal"] = disposal.upper()
    if decided_from:
        where.append("decision_date >= %(decided_from)s")
        params["decided_from"] = decided_from
    if decided_to:
        where.append("decision_date <= %(decided_to)s")
        params["decided_to"] = decided_to

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    order_sql = (
        "ORDER BY ts_rank(search, websearch_to_tsquery('english', %(q)s)) DESC, decision_date DESC"
        if q else "ORDER BY decision_date DESC NULLS LAST, id DESC"
    )
    params.update(limit=page_size, offset=(page - 1) * page_size)

    total = conn.execute(f"SELECT count(*) AS n FROM judgments {where_sql}", params).fetchone()["n"]
    rows = conn.execute(
        f"SELECT {LIST_COLUMNS} FROM judgments {where_sql} {order_sql} "
        "LIMIT %(limit)s OFFSET %(offset)s",
        params,
    ).fetchall()
    return {"total": total, "page": page, "page_size": page_size, "equivalents": added,
            "results": [_present(r) for r in rows]}


@app.get("/judgments/{judgment_id}")
def get_judgment(judgment_id: int, conn=Depends(get_conn)):
    row = conn.execute(
        f"SELECT {LIST_COLUMNS}, date_of_registration, description, full_text, text_language, "
        "parties, advocates, cases_cited, key_reasoning "
        "FROM judgments WHERE id = %s",
        (judgment_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "judgment not found")
    row["cited_by"] = conn.execute(CITED_BY_SQL, {"id": judgment_id, "limit": CITED_BY_LIMIT}).fetchall()
    row["cited_by_total"] = row["cited_by"][0]["total"] if row["cited_by"] else 0
    row["cites"] = conn.execute(CITES_SQL, (judgment_id,)).fetchall()
    row["citation"] = citation_line(row)
    return _present(row)


# Long names used when citing; other codes fall back to the headline names, then the code.
_CITE_KINDS = {"CW": "Civil Writ Petition", "SAW": "Special Appeal (Writ)", "HC": "Habeas Corpus Petition"}


_SMALL_WORDS = {"of", "and", "the", "for", "through", "thr", "by", "in"}


def _party(name: str | None, others: list | None) -> str:
    words = (name or "").split()
    name = " ".join(w.lower() if i and w.lower() in _SMALL_WORDS
                    else w.capitalize() if w.isupper() or w.islower() else w for i, w in enumerate(words))
    return f"{name} & Ors." if name and others and len(others) > 1 else name


def citation_line(j: dict) -> str:
    """Ready-to-cite line, e.g. 'Ladu v. State, 2024:RJ-JP:2823 (Raj.) [S.B. Criminal Appeal No. 28/1994,
    decided on 20.05.2024, Jaipur Bench]'."""
    parties = j.get("parties") or {}
    pet = _party(j.get("petitioner"), parties.get("petitioners"))
    res = _party(j.get("respondent"), parties.get("respondents"))
    if pet and res:
        name = f"{pet} v. {res}"
    else:
        name = re.sub(r"\s+Vs\.?\s+", " v. ", re.sub(r"^\S+ of ", "", j.get("title") or ""), flags=re.I)
    head = f"{name}, {j['neutral_citation']} (Raj.)" if j.get("neutral_citation") else f"{name} (Raj.)"
    details = []
    if j.get("case_type") and j.get("case_number") is not None:
        kind = _CITE_KINDS.get(j["case_type"]) or CASE_KINDS.get(j["case_type"], j["case_type"]).title()
        prefix = {"single": "S.B. ", "division": "D.B. "}.get(j.get("bench_strength") or "", "")
        details.append(f"{prefix}{kind} No. {j['case_number']}/{j['case_year']}")
    if j.get("decision_date"):
        details.append(f"decided on {j['decision_date']:%d.%m.%Y}")
    if j.get("bench"):
        details.append(f"{j['bench'].title()} Bench")
    return f"{head} [{', '.join(details)}]" if details else head


CITED_BY_LIMIT = 200
LINK_COLUMNS = "a.id, a.title, a.bench, a.case_type, a.case_number, a.case_year, a.decision_date, a.neutral_citation"

# One common order decides a batch of connected cases, each with its own row and the same
# text: show the order once, with how many connected cases it also decided.
CITED_BY_SQL = f"""
SELECT *, count(*) OVER () AS total FROM (
    SELECT DISTINCT ON (k.order_key) {LINK_COLUMNS},
           count(*) OVER (PARTITION BY k.order_key) - 1 AS connected
    FROM citations c
    JOIN judgments a ON a.id = c.citing_id
    CROSS JOIN LATERAL (SELECT coalesce(a.neutral_citation, md5(a.full_text), a.id::text) AS order_key) k
    WHERE c.cited_id = %(id)s
    ORDER BY k.order_key, a.id
) orders
ORDER BY decision_date DESC NULLS LAST, id DESC
LIMIT %(limit)s
"""

CITES_SQL = f"""
SELECT {LINK_COLUMNS}
FROM citations c JOIN judgments a ON a.id = c.cited_id
WHERE c.citing_id = %s
ORDER BY a.decision_date DESC NULLS LAST, a.id DESC
"""


@app.get("/stats")
def stats(conn=Depends(get_conn)):
    """Counts that help a user orient: totals, top judges, acts, case types, outcomes."""
    return {
        "total": conn.execute("SELECT count(*) AS n FROM judgments").fetchone()["n"],
        "with_text": conn.execute(
            "SELECT count(*) AS n FROM judgments WHERE full_text IS NOT NULL").fetchone()["n"],
        "by_bench": conn.execute(
            "SELECT bench, count(*) AS n FROM judgments GROUP BY 1 ORDER BY 2 DESC").fetchall(),
        "top_judges": conn.execute(
            "SELECT j AS judge, count(*) AS n FROM judgments, unnest(judges) j "
            "GROUP BY 1 ORDER BY 2 DESC LIMIT 20").fetchall(),
        "top_acts": conn.execute(
            "SELECT a->>'act' AS act, count(*) AS n FROM judgments, jsonb_array_elements(acts_cited) a "
            "GROUP BY 1 ORDER BY 2 DESC LIMIT 100").fetchall(),
        "top_case_types": conn.execute(
            "SELECT case_type, count(*) AS n FROM judgments WHERE case_type IS NOT NULL "
            "GROUP BY 1 ORDER BY 2 DESC LIMIT 20").fetchall(),
        "by_disposal": conn.execute(
            "SELECT disposal_nature, count(*) AS n FROM judgments "
            "GROUP BY 1 ORDER BY 2 DESC LIMIT 20").fetchall(),
    }
