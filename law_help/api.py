"""Search API over imported judgments.

Run with: uvicorn law_help.api:app --reload
"""

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from psycopg.types.json import Jsonb

from . import bareacts, db, feed
from .acts_api import router as acts_router
from .extract import CASE_KINDS, canonical_act, headline
from .importer import pdf_url
from .landmark import landmark_sql
from .status_links import status_links
from .supreme import COURT_NAME as SUPREME_COURT

app = FastAPI(title="law_help", description="Search Supreme Court and Rajasthan High Court judgments")

COURTS = {"supreme": SUPREME_COURT, "rajasthan": "Rajasthan High Court"}

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
    id, source, court, report_citation, bench, cnr, case_type, case_number, case_year, title, petitioner, respondent,
    judges, bench_judges, bench_strength, disposal_nature, decision_date, pdf_key,
    neutral_citation, summary, ai_summary, outcome, acts_cited,
    (SELECT t.kind FROM treatments t WHERE t.judgment_id = judgments.id
     ORDER BY array_position(ARRAY['set_aside', 'recalled', 'overruled', 'partly_set_aside'], t.kind)
     LIMIT 1) AS good_law,
    coalesce((SELECT n FROM cited_counts WHERE judgment_id = judgments.id), 0) AS cited_by_count,
    """ + landmark_sql() + """ AS landmark
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
    row["pdf_url"] = pdf_url(row.pop("source"), row.pop("pdf_key"))
    row["headline"] = headline(row["case_type"], row["acts_cited"], row["outcome"], row["disposal_nature"])
    return row


@dataclass
class Filters:
    """A search's WHERE clause and params, shared by /judgments and /feed."""
    where_sql: str
    params: dict
    equivalents: list   # the old- or new-code sections also searched (IPC 302 -> BNS 103)
    q: str | None
    landmark: bool


def judgment_filters(
    q: str | None = Query(None, description="full-text search (web-search syntax)"),
    judge: str | None = Query(None, description="exact judge name, e.g. SAMEER JAIN"),
    act: str | None = Query(None, description="act cited, e.g. IPC or Indian Penal Code, 1860"),
    section: str | None = Query(None, description="section of `act`, e.g. 302 or 120-B"),
    equivalent: bool = Query(True, description="also match the same section in the old or new code "
                             "(IPC 302 also finds BNS 103)"),
    case_type: str | None = Query(None, description="e.g. CW, CRLMB"),
    court: str | None = Query(None, pattern="^(supreme|rajasthan)$", description="supreme or rajasthan (HC)"),
    bench: str | None = Query(None, pattern="^(jaipur|jodhpur)$"),
    disposal: str | None = Query(None, description="e.g. ALLOWED, DISMISSED"),
    decided_from: date | None = None,
    decided_to: date | None = None,
    landmark: bool = Query(False, description="only judgments cited by many later ones, most cited first"),
) -> Filters:
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
    if court:
        where.append("court = %(court)s")
        params["court"] = COURTS[court]
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

    if landmark:
        # cited_counts is small, so narrow to it first
        where.append("id IN (SELECT judgment_id FROM cited_counts "
                     "WHERE n >= (SELECT coalesce(min(n), 2147483647) FROM landmark_thresholds))")
        where.append(landmark_sql())

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    return Filters(where_sql, params, added, q, landmark)


@app.get("/judgments")
def search_judgments(
    filters: Filters = Depends(judgment_filters),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    conn=Depends(get_conn),
):
    where_sql, params, added = filters.where_sql, dict(filters.params), filters.equivalents
    order_sql = (
        "ORDER BY ts_rank(search, websearch_to_tsquery('english', %(q)s)) DESC, decision_date DESC"
        if filters.q else "ORDER BY cited_by_count DESC, decision_date DESC NULLS LAST, id DESC"
        if filters.landmark else "ORDER BY decision_date DESC NULLS LAST, id DESC"
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
    row["treated_by"] = conn.execute(TREATED_BY_SQL, (judgment_id,)).fetchall()
    row["citation"] = citation_line(row)
    row["status_links"] = status_links(row)
    return _present(row)


# Long names used when citing; other codes fall back to the headline names, then the code.
_CITE_KINDS = {"CW": "Civil Writ Petition", "SAW": "Special Appeal (Writ)", "HC": "Habeas Corpus Petition"}


_SMALL_WORDS = {"of", "and", "the", "for", "through", "thr", "by", "in"}


def _party(name: str | None, others: list | None) -> str:
    words = (name or "").split()
    name = " ".join(w.lower() if i and w.lower() in _SMALL_WORDS
                    else w.capitalize() if w.isupper() or w.islower() else w for i, w in enumerate(words))
    return f"{name} & Ors." if name and others and len(others) > 1 else name


def case_name(j: dict) -> str:
    """'Ladu v. State', from the parties, else from the title."""
    parties = j.get("parties") or {}
    pet = _party(j.get("petitioner"), parties.get("petitioners"))
    res = _party(j.get("respondent"), parties.get("respondents"))
    if pet and res:
        return f"{pet} v. {res}"
    return re.sub(r"\s+Vs\.?\s+", " v. ", re.sub(r"^\S+ of ", "", j.get("title") or ""), flags=re.I)


def citation_line(j: dict) -> str:
    """Ready-to-cite line, e.g. 'Ladu v. State, 2024:RJ-JP:2823 (Raj.) [S.B. Criminal Appeal No. 28/1994,
    decided on 20.05.2024, Jaipur Bench]'."""
    name = case_name(j)
    if j.get("court") == SUPREME_COURT:
        # "Vijay Singh v. The State of Bihar, 2024 INSC 735 : [2024] 10 S.C.R. 108 [Criminal Appeal
        # No. 1031 of 2015, decided on 25.09.2024]"
        cites = " : ".join(c for c in (j.get("neutral_citation"), j.get("report_citation")) if c)
        details = []
        if j.get("case_type") and j.get("case_number") is not None:
            details.append(f"{j['case_type'].title()} No. {j['case_number']} of {j['case_year']}")
        if j.get("decision_date"):
            details.append(f"decided on {j['decision_date']:%d.%m.%Y}")
        head = f"{name}, {cites}" if cites else name
        return f"{head} [{', '.join(details)}]" if details else head
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
LINK_COLUMNS = "a.id, a.title, a.court, a.bench, a.case_type, a.case_number, a.case_year, a.decision_date, a.neutral_citation"

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

# Later judgments that set this one aside, recalled or overruled it (law_help.goodlaw).
TREATED_BY_SQL = f"""
SELECT {LINK_COLUMNS}, t.kind, t.quote
FROM treatments t JOIN judgments a ON a.id = t.by_id
WHERE t.judgment_id = %s
ORDER BY a.decision_date DESC NULLS LAST, a.id DESC
"""


@app.get("/stats")
def stats(conn=Depends(get_conn)):
    """Counts that help a user orient: totals, top judges, acts, case types, outcomes."""
    return {
        "total": conn.execute("SELECT count(*) AS n FROM judgments").fetchone()["n"],
        "with_text": conn.execute(
            "SELECT count(*) AS n FROM judgments WHERE full_text IS NOT NULL").fetchone()["n"],
        "by_court": conn.execute(
            "SELECT court, count(*) AS n FROM judgments GROUP BY 1 ORDER BY 2 DESC").fetchall(),
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


@app.get("/feed", response_class=Response,
         responses={200: {"content": {"application/rss+xml": {}}, "description": "RSS 2.0 feed"}})
def search_feed(request: Request, filters: Filters = Depends(judgment_filters), conn=Depends(get_conn)):
    """The same search as /judgments as an RSS feed: the newest 50 matches by when law_help added
    them, so a feed reader picks up each update's new judgments."""
    rows = conn.execute(
        f"SELECT {LIST_COLUMNS}, parties, added_at FROM judgments {filters.where_sql} "
        f"ORDER BY added_at DESC, decision_date DESC NULLS LAST, id DESC LIMIT {feed.FEED_SIZE}",
        filters.params,
    ).fetchall()
    items = []
    for j in map(_present, rows):
        name = case_name(j) or j["title"]
        summary = (j["ai_summary"] or {}).get("summary") or j["summary"]
        items.append({
            "title": f"{name}: {j['headline']}" if j["headline"] else name,
            "link": str(request.url_for("judgment_page").include_query_params(id=j["id"])),
            "description": "\n\n".join(filter(None, [citation_line(j), summary])),
            "guid": j["id"],
            "added": j["added_at"],
        })
    search = str(request.url_for("search_page")) + (f"?{request.url.query}" if request.url.query else "")
    body = feed.render(feed.feed_title(dict(request.query_params)), search, str(request.url), items)
    return Response(body, media_type="application/rss+xml; charset=utf-8")
