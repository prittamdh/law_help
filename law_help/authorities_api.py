"""Citation lines for many judgments at once: GET /judgments/citations?ids=1,2,3.

The saved page uses this to build a list of authorities for a folder without fetching
each judgment's full text.
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from . import db

router = APIRouter()

MAX_IDS = 500

# Only what citation_line reads, plus what the list is sorted by.
CITATION_COLUMNS = """
    id, court, bench, bench_strength, title, petitioner, respondent, parties, case_type, case_number,
    case_year, decision_date, neutral_citation, report_citation
"""


def get_conn():
    with db.connect() as conn:
        yield conn


@router.get("/judgments/citations")
def judgment_citations(
    ids: str = Query(..., description="comma-separated judgment ids, e.g. 12,40,7"),
    conn=Depends(get_conn),
):
    """Ready-to-cite lines in the order asked; ids not found are left out."""
    from .api import SUPREME_COURT, citation_line
    try:
        wanted = list(dict.fromkeys(int(i) for i in ids.split(",") if i.strip()))
    except ValueError:
        raise HTTPException(422, "ids must be comma-separated numbers")
    if len(wanted) > MAX_IDS:
        raise HTTPException(422, f"at most {MAX_IDS} ids")
    rows = conn.execute(f"SELECT {CITATION_COLUMNS} FROM judgments WHERE id = ANY(%s)", (wanted,)).fetchall()
    by_id = {r["id"]: r for r in rows}
    out = []
    for r in (by_id[i] for i in wanted if i in by_id):
        # With only the party fields and no citation or case details, citation_line gives just the name.
        name = citation_line({k: r[k] for k in ("title", "petitioner", "respondent", "parties")}
                             | {"court": SUPREME_COURT})
        out.append({"id": r["id"], "court": r["court"], "decision_date": r["decision_date"],
                    "case_name": name, "citation": citation_line(r)})
    return out
