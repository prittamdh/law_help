"""Search API over imported judgments.

Run with: uvicorn law_help.api:app --reload
"""

from datetime import date

from fastapi import Depends, FastAPI, HTTPException, Query

from . import db
from .importer import BUCKET_URL

app = FastAPI(title="law_help", description="Search Rajasthan High Court judgments")

LIST_COLUMNS = """
    id, bench, cnr, case_type, case_number, case_year, title, petitioner, respondent,
    judges, bench_strength, disposal_nature, decision_date, pdf_key
"""


def get_conn():
    with db.connect() as conn:
        yield conn


def _with_pdf_url(row: dict) -> dict:
    row["pdf_url"] = f"{BUCKET_URL}/{row.pop('pdf_key')}"
    return row


@app.get("/judgments")
def search_judgments(
    q: str | None = Query(None, description="full-text search (web-search syntax)"),
    judge: str | None = Query(None, description="exact judge name, e.g. SAMEER JAIN"),
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
        where.append("%(judge)s = ANY(judges)")
        params["judge"] = judge.upper()
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
    return {"total": total, "page": page, "page_size": page_size,
            "results": [_with_pdf_url(r) for r in rows]}


@app.get("/judgments/{judgment_id}")
def get_judgment(judgment_id: int, conn=Depends(get_conn)):
    row = conn.execute(
        f"SELECT {LIST_COLUMNS}, date_of_registration, description, full_text "
        "FROM judgments WHERE id = %s",
        (judgment_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "judgment not found")
    return _with_pdf_url(row)


@app.get("/stats")
def stats(conn=Depends(get_conn)):
    """Counts that help a user orient: totals, top judges, case types, outcomes."""
    return {
        "total": conn.execute("SELECT count(*) AS n FROM judgments").fetchone()["n"],
        "with_text": conn.execute(
            "SELECT count(*) AS n FROM judgments WHERE full_text IS NOT NULL").fetchone()["n"],
        "by_bench": conn.execute(
            "SELECT bench, count(*) AS n FROM judgments GROUP BY 1 ORDER BY 2 DESC").fetchall(),
        "top_judges": conn.execute(
            "SELECT j AS judge, count(*) AS n FROM judgments, unnest(judges) j "
            "GROUP BY 1 ORDER BY 2 DESC LIMIT 20").fetchall(),
        "top_case_types": conn.execute(
            "SELECT case_type, count(*) AS n FROM judgments WHERE case_type IS NOT NULL "
            "GROUP BY 1 ORDER BY 2 DESC LIMIT 20").fetchall(),
        "by_disposal": conn.execute(
            "SELECT disposal_nature, count(*) AS n FROM judgments "
            "GROUP BY 1 ORDER BY 2 DESC LIMIT 20").fetchall(),
    }
