"""Bare acts API: /acts, /acts/{act}, /acts/{act}/{section}, and the /acts page."""

from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from psycopg.types.json import Jsonb

from . import bareacts, counts as stored, db
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
        f"SELECT id, title, court, bench, case_type, case_number, case_year, decision_date, neutral_citation, pdf_key "
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


# Section pages: every judgment citing a section, in the old or the new code, most cited first.

def _where(frags: list[dict], court: str | None, table: str = "") -> tuple[str, dict]:
    params = {f"f{i}": Jsonb([f]) for i, f in enumerate(frags)}
    where = "(" + " OR ".join(f"{table}acts_cited @> %(f{i})s" for i in range(len(frags))) + ")"
    if court:
        from .api import COURTS
        where += f" AND {table}court = %(court)s"
        params["court"] = COURTS[court]
    return where, params


@router.get("/api/acts/{slug}/sections/{number}/judgments")
def section_judgments(slug: str, number: str, conn=Depends(get_conn),
                      court: str | None = Query(None, pattern="^(supreme|rajasthan)$"),
                      page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    """Judgments citing a section or its counterpart in the old or new code (IPC 420 with BNS 318(4)),
    most cited first, then newest."""
    from .api import LIST_COLUMNS, _present
    act = _act(slug)
    sec = bareacts.get_section(slug, number)
    if sec is None:
        raise HTTPException(404, "no such section")
    frags, added = bareacts.search_forms(act["act"], sec["number"])
    where, params = _where(frags, court)
    total = conn.execute(f"SELECT count(*) AS n FROM judgments WHERE {where}", params).fetchone()["n"]
    rows = conn.execute(
        f"SELECT {LIST_COLUMNS} FROM judgments WHERE {where} "
        "ORDER BY cited_by_count DESC, decision_date DESC NULLS LAST, id DESC LIMIT %(limit)s OFFSET %(offset)s",
        {**params, "limit": page_size, "offset": (page - 1) * page_size}).fetchall()
    return {"act": {"slug": slug, "act": act["act"], "short": act["short"], "kind": act["kind"]},
            "number": sec["number"], "title": sec["title"], "equivalents": added,
            "total": total, "page": page, "page_size": page_size, "results": [_present(r) for r in rows]}


@lru_cache(maxsize=16)
def _form_targets(slug: str) -> tuple[list[str], list[str], list[str]]:
    """Three columns: act name, section as judgments cite it, and the section of `slug` it counts
    toward. Built with search_forms, so a count on the contents page matches its section page."""
    act = _act(slug)
    names, cited, targets = [], [], []
    for s in act["sections"]:
        for f in bareacts.search_forms(act["act"], s["number"])[0]:
            names.append(f["act"])
            cited.append(f["sections"][0])
            targets.append(s["number"])
    return names, cited, targets




@router.get("/api/acts/{slug}/judgment-counts")
def section_judgment_counts(slug: str, conn=Depends(get_conn),
                            court: str | None = Query(None, pattern="^(supreme|rajasthan)$")):
    """How many judgments cite each section of an act, old and new code together: {"420": 12, ...}.
    Sections no judgment cites are left out."""
    _act(slug)
    return stored.cached(conn, f"sections:{slug}:{court or ''}",
                         lambda: compute_section_counts(conn, slug, court))


def compute_section_counts(conn, slug: str, court: str | None) -> dict:
    names, cited, targets = _form_targets(slug)
    where, params = _where([{"act": a} for a in dict.fromkeys(names)], court, table="j.")
    rows = conn.execute(
        "SELECT m.target, count(DISTINCT j.id) AS n "
        "FROM judgments j, jsonb_array_elements(j.acts_cited) a, jsonb_array_elements_text(a->'sections') s, "
        "unnest(%(m_act)s::text[], %(m_cited)s::text[], %(m_target)s::text[]) AS m(act, cited, target) "
        f"WHERE {where} AND a->>'act' = m.act AND s = m.cited GROUP BY 1",
        {**params, "m_act": names, "m_cited": cited, "m_target": targets}).fetchall()
    return {r["target"]: r["n"] for r in rows}
