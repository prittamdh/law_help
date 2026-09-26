"""Bare acts API: /acts, /acts/{act}, /acts/{act}/{section}, and the /acts page."""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from psycopg.types.json import Jsonb

from . import bareacts, db
from .importer import BUCKET_URL

router = APIRouter()


def get_conn():
    with db.connect() as conn:
        yield conn


@router.get("/acts", include_in_schema=False)
def acts_page(act: str | None = None):
    from .api import STATIC_DIR
    return FileResponse(STATIC_DIR / "acts.html")


@router.get("/api/acts")
def list_acts():
    return [{"slug": slug, "act": a["act"], "short": a["short"], "kind": a["kind"], "source": a["source"],
             "sections": len(a["sections"]), "replaced_by": bareacts.REPLACED.get(slug),
             "replaces": next((o for o, n in bareacts.REPLACED.items() if n == slug), None)}
            for slug, a in bareacts.acts().items()]


def _act(slug: str) -> dict:
    act = bareacts.acts().get(slug)
    if not act:
        raise HTTPException(404, "no such act")
    return act


@router.get("/api/acts/{slug}")
def get_act(slug: str):
    act = _act(slug)
    return {"slug": slug, "act": act["act"], "short": act["short"], "kind": act["kind"],
            "source": act["source"], "replaced_by": bareacts.REPLACED.get(slug),
            "sections": [{"number": s["number"], "title": s["title"], "chapter": s["chapter"]}
                         for s in act["sections"]]}


CITING_LIMIT = 10


@router.get("/api/acts/{slug}/sections/{number}")
def get_section(slug: str, number: str, conn=Depends(get_conn),
                limit: int = Query(CITING_LIMIT, ge=0, le=50)):
    """A section's text, its counterpart in the old or new code, and judgments that cite it."""
    act = _act(slug)
    sec = bareacts.get_section(slug, number)
    if sec is None:
        raise HTTPException(404, "no such section")
    idx = act["sections"].index(sec)
    forms = bareacts.cited_forms(slug, sec["number"])
    where = " OR ".join(f"acts_cited @> %(f{i})s" for i in range(len(forms)))
    params = {f"f{i}": Jsonb([{"act": act["act"], "sections": [f]}]) for i, f in enumerate(forms)}
    total = conn.execute(f"SELECT count(*) AS n FROM judgments WHERE {where}", params).fetchone()["n"]
    rows = conn.execute(
        f"SELECT id, title, bench, case_type, case_number, case_year, decision_date, neutral_citation, pdf_key "
        f"FROM judgments WHERE {where} ORDER BY decision_date DESC NULLS LAST, id DESC LIMIT %(limit)s",
        {**params, "limit": limit}).fetchall()
    for r in rows:
        r["pdf_url"] = f"{BUCKET_URL}/{r.pop('pdf_key')}"
    neighbour = lambda i: ({"number": act["sections"][i]["number"], "title": act["sections"][i]["title"]}
                           if 0 <= i < len(act["sections"]) else None)
    return {"act": {"slug": slug, "act": act["act"], "short": act["short"], "kind": act["kind"],
                    "source": act["source"]},
            **sec, "source": sec.get("source") or act["source"],
            "equivalents": bareacts.equivalents(slug, sec["number"]),
            "previous": neighbour(idx - 1), "next": neighbour(idx + 1),
            "cited_by": {"total": total, "results": rows}}
