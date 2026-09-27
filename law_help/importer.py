"""Import Rajasthan High Court judgments from the public AWS open-data bucket.

Supreme Court judgments come from a sibling bucket; law_help.supreme reads that one.

Dataset: https://registry.opendata.aws/indian-high-court-judgments/ (CC-BY-4.0).
Layout per year and bench:
    metadata/parquet/year=YYYY/court=8_9/bench=<bench>/metadata.parquet
    data/pdf/year=YYYY/court=8_9/bench=<bench>/<file>.pdf
"""

import argparse
import io
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import httpx
import pyarrow.parquet as pq

from . import db, extract, landmark, supreme, treatment
from .goodlaw import link_treatments
from .parse import BENCHES, bench_strength, parse_date, parse_judges, parse_title

BUCKET_URL = "https://indian-high-court-judgments.s3.ap-south-1.amazonaws.com"
COURT_CODE = "8_9"
COURT_NAME = "Rajasthan High Court"
SOURCE = "aws-hc-judgments"
CACHE_DIR = Path(".cache")

log = logging.getLogger("law_help.importer")


def bucket_url(source: str) -> str:
    """Where a row's pdf_key lives."""
    return supreme.BUCKET_URL if source == supreme.SOURCE else BUCKET_URL


def pdf_url(source: str, pdf_key: str) -> str:
    return f"{bucket_url(source)}/{pdf_key}"


def is_supreme(source: str) -> bool:
    return source == supreme.SOURCE

UPSERT_SQL = """
INSERT INTO judgments (
    source, court, bench, cnr, pdf_link, pdf_key, case_type, case_number, case_year,
    title, petitioner, respondent, judges, bench_strength, disposal_nature,
    date_of_registration, decision_date, description
) VALUES (
    %(source)s, %(court)s, %(bench)s, %(cnr)s, %(pdf_link)s, %(pdf_key)s, %(case_type)s,
    %(case_number)s, %(case_year)s, %(title)s, %(petitioner)s, %(respondent)s, %(judges)s,
    %(bench_strength)s, %(disposal_nature)s, %(date_of_registration)s, %(decision_date)s,
    %(description)s
)
ON CONFLICT (pdf_link) DO UPDATE SET
    cnr = EXCLUDED.cnr, pdf_key = EXCLUDED.pdf_key, case_type = EXCLUDED.case_type,
    case_number = EXCLUDED.case_number, case_year = EXCLUDED.case_year,
    title = EXCLUDED.title, petitioner = EXCLUDED.petitioner,
    respondent = EXCLUDED.respondent, judges = EXCLUDED.judges,
    bench_strength = EXCLUDED.bench_strength, disposal_nature = EXCLUDED.disposal_nature,
    date_of_registration = EXCLUDED.date_of_registration,
    decision_date = EXCLUDED.decision_date, description = EXCLUDED.description,
    imported_at = now()
"""


def list_partitions(client: httpx.Client, years: list[int] | None = None) -> list[tuple[int, str]]:
    """Return (year, bench_code) pairs that exist in the bucket for Rajasthan."""
    found = []
    for year in years or range(1950, datetime.now().year + 1):
        prefix = f"metadata/parquet/year={year}/court={COURT_CODE}/"
        r = client.get(BUCKET_URL, params={"list-type": "2", "prefix": prefix, "delimiter": "/"})
        r.raise_for_status()
        for bench in re.findall(r"bench=([^/<]+)/", r.text):
            found.append((year, bench))
    return found


def fetch_metadata(client: httpx.Client, year: int, bench_code: str) -> bytes:
    key = f"metadata/parquet/year={year}/court={COURT_CODE}/bench={bench_code}/metadata.parquet"
    cached = CACHE_DIR / key
    if cached.exists():
        return cached.read_bytes()
    r = client.get(f"{BUCKET_URL}/{key}")
    r.raise_for_status()
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(r.content)
    return r.content


def to_records(parquet_bytes: bytes, year: int, bench_code: str) -> list[dict]:
    table = pq.read_table(io.BytesIO(parquet_bytes), columns=[
        "title", "description", "judge", "pdf_link", "cnr",
        "date_of_registration", "decision_date", "disposal_nature",
    ])
    records = []
    for row in table.to_pylist():
        if not row["pdf_link"]:
            continue
        judges = parse_judges(row["judge"])
        filename = row["pdf_link"].rsplit("/", 1)[-1]
        records.append({
            "source": SOURCE,
            "court": COURT_NAME,
            "bench": BENCHES.get(bench_code, bench_code),
            "cnr": row["cnr"],
            "pdf_link": row["pdf_link"],
            "pdf_key": f"data/pdf/year={year}/court={COURT_CODE}/bench={bench_code}/{filename}",
            "title": (row["title"] or "").strip(),
            "judges": judges,
            "bench_strength": bench_strength(judges),
            "disposal_nature": (row["disposal_nature"] or "").strip() or None,
            "date_of_registration": parse_date(row["date_of_registration"]),
            "decision_date": parse_date(row["decision_date"]),
            "description": row["description"],
            **parse_title(row["title"]),
        })
    return records


def import_metadata(years: list[int] | None, benches: list[str] | None) -> int:
    total = 0
    with httpx.Client(timeout=120) as client, db.connect() as conn:
        db.init_schema(conn)
        for year, bench_code in list_partitions(client, years):
            if benches and BENCHES.get(bench_code, bench_code) not in benches:
                continue
            records = to_records(fetch_metadata(client, year, bench_code), year, bench_code)
            with conn.cursor() as cur:
                cur.executemany(UPSERT_SQL, records)
            conn.commit()
            total += len(records)
            log.info("imported %d judgments for %s %d", len(records), bench_code, year)
    return total


# The Supreme Court's metadata also carries its neutral citation ("2024 INSC 735") and the
# Supreme Court Reports citation, which older judgments don't print.
SC_UPSERT_SQL = UPSERT_SQL.replace(
    "description\n) VALUES", "description, neutral_citation, report_citation\n) VALUES"
).replace(
    "%(description)s\n)", "%(description)s, %(neutral_citation)s, %(report_citation)s\n)"
).replace(
    "imported_at = now()",
    "neutral_citation = EXCLUDED.neutral_citation, report_citation = EXCLUDED.report_citation, imported_at = now()",
)


def fetch_sc_metadata(client: httpx.Client, year: int) -> bytes | None:
    key = supreme.metadata_key(year)
    cached = CACHE_DIR / "supreme" / key
    if cached.exists():
        return cached.read_bytes()
    r = client.get(f"{supreme.BUCKET_URL}/{key}")
    if r.status_code in (403, 404):
        return None
    r.raise_for_status()
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(r.content)
    return r.content


def import_supreme(years: list[int] | None) -> int:
    """Supreme Court judgment metadata, every year by default (43,500 rows, about 1 minute)."""
    total = 0
    with httpx.Client(timeout=120) as client, db.connect() as conn:
        db.init_schema(conn)
        for year in years or range(supreme.FIRST_YEAR, datetime.now().year + 1):
            data = fetch_sc_metadata(client, year)
            if data is None:
                continue
            records = supreme.to_records(data, year)
            with conn.cursor() as cur:
                cur.executemany(SC_UPSERT_SQL, records)
            conn.commit()
            total += len(records)
            log.info("imported %d Supreme Court judgments for %d", len(records), year)
    return total


STRUCTURE_SQL = """
UPDATE judgments SET
    text_language = %(language)s,
    -- a Supreme Court row keeps the citation from its metadata; old reports don't print one
    neutral_citation = CASE WHEN source = 'aws-sc-judgments' THEN neutral_citation ELSE %(neutral_citation)s END,
    parties = %(parties)s, advocates = %(advocates)s, bench_judges = %(judges)s,
    acts_cited = %(acts_cited)s, cases_cited = %(cases_cited)s, case_refs = %(case_refs)s,
    summary = %(summary)s,
    outcome = %(outcome)s, key_reasoning = %(key_reasoning)s,
    extractor_version = %(extractor_version)s, structured_at = now()
WHERE id = %(id)s
"""


def _structure_params(judgment_id: int, text: str | None, fields: dict | None = None,
                      sc: bool = False) -> dict:
    from psycopg.types.json import Jsonb

    fields = fields or extract.extract(text or "", sc)
    return {
        "id": judgment_id,
        "language": fields["language"],
        "neutral_citation": fields["neutral_citation"],
        "parties": Jsonb({"petitioners": fields["petitioners"], "respondents": fields["respondents"]}),
        "advocates": Jsonb(fields["advocates"]),
        "judges": fields["judges"],
        "acts_cited": Jsonb(fields["acts_cited"]),
        "cases_cited": Jsonb(fields["cases_cited"]),
        "case_refs": fields["case_refs"],
        "summary": fields["summary"],
        "outcome": fields["outcome"],
        "key_reasoning": fields["key_reasoning"],
        "extractor_version": fields["extractor_version"],
    }


def _extract_many(items: list[tuple[str | None, bool]]) -> list[dict]:
    return [extract.extract(t or "", sc) for t, sc in items]


def structure(limit: int | None, redo: bool, workers: int | None = None) -> int:
    """Re-derive structured fields from stored text; no downloads, all CPU cores.

    By default only rows never structured, or structured by an older extractor version.
    """
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    workers = os.cpu_count() if workers is None else workers
    done = 0
    with db.connect() as conn, db.connect() as reader:
        db.init_schema(conn)
        where = "full_text IS NOT NULL"
        if not redo:
            where += " AND (extractor_version IS NULL OR extractor_version < %(v)s)"
        # A named cursor streams rows instead of loading every text into memory.
        cur = reader.cursor(name="structure")
        cur.execute(f"SELECT id, source, full_text FROM judgments WHERE {where} ORDER BY id "
                    + ("LIMIT %(limit)s" if limit else ""),
                    {"v": extract.EXTRACTOR_VERSION, "limit": limit})
        pool = ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("forkserver")) \
            if workers > 1 else None
        try:
            while rows := cur.fetchmany(workers * 50):
                texts = [(r["full_text"], is_supreme(r["source"])) for r in rows]
                chunks = [texts[i:i + 50] for i in range(0, len(texts), 50)]
                results = [f for c in (pool.map(_extract_many, chunks) if pool else map(_extract_many, chunks))
                           for f in c]
                with conn.cursor() as w:
                    w.executemany(STRUCTURE_SQL, [_structure_params(r["id"], None, f)
                                                  for r, f in zip(rows, results)])
                conn.commit()
                done += len(rows)
        finally:
            if pool:
                pool.shutdown()
        cur.close()
    return done


# Each ref goes to the latest judgment in that case decided before the citing one, so a
# case's final order is preferred over its interim orders. Jaipur and Jodhpur reuse case
# numbers, so a wrong link is easy: when the ref has party words, the case's title must
# contain one; without them, the case number must be one bench's only, or the text must
# name the seat. Anything else stays unlinked.
LINK_CASES_SQL = r"""
INSERT INTO citations (citing_id, cited_id)
SELECT DISTINCT j.id, pick.id
FROM judgments j
CROSS JOIN LATERAL unnest(j.case_refs) AS ref
CROSS JOIN LATERAL (SELECT split_part(ref, '/', 4) AS words, split_part(ref, '/', 5) AS seat) h
CROSS JOIN LATERAL (
    SELECT c.id FROM (
        SELECT t.id, t.bench, t.decision_date, t.named,
               min(t.bench) OVER () <> max(t.bench) OVER () AS two_benches
        FROM (
            SELECT t.id, t.bench, t.decision_date,
                   h.words <> '' AND t.title ~* ('\m(' || replace(h.words, ' ', '|') || ')\M') AS named
            FROM judgments t
            WHERE t.case_type = ANY(string_to_array(split_part(ref, '/', 1), '|'))
              AND t.case_number = split_part(ref, '/', 2)::int
              AND t.case_year = split_part(ref, '/', 3)::int
              AND (j.decision_date IS NULL OR t.decision_date < j.decision_date)
              AND (t.case_type, t.case_number, t.case_year)
                  IS DISTINCT FROM (j.case_type, j.case_number, j.case_year)
        ) t
    ) c
    WHERE c.named
       OR (h.words = '' AND (h.seat = '' OR c.bench = h.seat) AND (h.seat <> '' OR NOT c.two_benches))
    ORDER BY c.named DESC, c.decision_date DESC NULLS LAST, c.id DESC
    LIMIT 1
) pick
WHERE j.case_refs <> '{}' AND left(ref, 3) <> 'NC:'
ON CONFLICT DO NOTHING
"""

LINK_NEUTRAL_SQL = """
INSERT INTO citations (citing_id, cited_id)
SELECT DISTINCT j.id, t.id
FROM judgments j
CROSS JOIN LATERAL unnest(j.case_refs) AS ref
JOIN judgments t ON t.neutral_citation IN (substr(ref, 4), substr(ref, 4) || '-DB', substr(ref, 4) || '-FB')
WHERE j.case_refs <> '{}' AND left(ref, 3) = 'NC:' AND t.id <> j.id
ON CONFLICT DO NOTHING
"""


def link_citations(conn=None) -> int:
    """Rebuild the citations table from every judgment's case_refs; returns the number of links."""
    if conn is None:
        with db.connect() as conn:
            return link_citations(conn)
    db.init_schema(conn)
    with conn.transaction():
        conn.execute("DELETE FROM citations")
        conn.execute(LINK_CASES_SQL)
        conn.execute(LINK_NEUTRAL_SQL)
        landmark.link_supreme(conn)
        landmark.count_citations(conn)
        treatment.label_citations(conn)
    return conn.execute("SELECT count(*) AS n FROM citations").fetchone()["n"]


HINDI_SQL = STRUCTURE_SQL.replace(
    "UPDATE judgments SET",
    "UPDATE judgments SET full_text = %(text)s, full_text_original = %(original)s,",
)


def _convert_many(texts: list[str]) -> list[tuple[str, str | None, dict]]:
    out = []
    for t in texts:
        text, original = extract.readable(t)
        out.append((text, original, extract.extract(text or "")))
    return out


def hindi(workers: int | None = None) -> int:
    """Convert stored Kruti Dev text to Unicode Hindi and re-derive those rows' fields.

    Takes every Hindi row and re-converts from the stored original, so it is safe to run
    again after the converter improves.
    """
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    workers = os.cpu_count() if workers is None else workers
    done = 0
    with db.connect() as conn, db.connect() as reader:
        db.init_schema(conn)
        cur = reader.cursor(name="hindi")
        cur.execute("SELECT id, coalesce(full_text_original, full_text) AS text FROM judgments "
                    "WHERE full_text IS NOT NULL AND (text_language IN ('hi-krutidev', 'hi') "
                    "OR full_text_original IS NOT NULL) ORDER BY id")
        pool = ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("forkserver")) \
            if workers > 1 else None
        try:
            while rows := cur.fetchmany(workers * 50):
                texts = [r["text"] for r in rows]
                chunks = [texts[i:i + 50] for i in range(0, len(texts), 50)]
                results = [x for c in (pool.map(_convert_many, chunks) if pool else map(_convert_many, chunks))
                           for x in c]
                params = []
                for r, (text, original, fields) in zip(rows, results):
                    p = _structure_params(r["id"], None, fields)
                    p.update(text=text, original=original)
                    params.append(p)
                with conn.cursor() as w:
                    w.executemany(HINDI_SQL, params)
                conn.commit()
                done += len(rows)
                if done % 5000 < len(rows):
                    log.info("converted %d Hindi judgments", done)
        finally:
            if pool:
                pool.shutdown()
        cur.close()
    return done


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m law_help.importer")
    sub = parser.add_subparsers(dest="command", required=True)

    meta = sub.add_parser("metadata", help="import judgment metadata")
    meta.add_argument("--year", type=int, action="append", help="repeatable; default all years")
    meta.add_argument("--bench", choices=sorted(set(BENCHES.values())), action="append",
                      help="repeatable; default both benches")

    sc = sub.add_parser("supreme", help="import Supreme Court judgment metadata (then run `text` for "
                                         "the judgments themselves)")
    sc.add_argument("--year", type=int, action="append", help="repeatable; default all years")

    text = sub.add_parser("text", help="download PDFs, extract full text and structure; "
                                       "resumable, so just run it again after a stop")
    text.add_argument("--limit", type=int,
                      help="only the newest N judgments without text; default every one")
    text.add_argument("--year", type=int, action="append", help="repeatable; default all years")
    text.add_argument("--workers", type=int, help="parser processes (default: one per CPU)")
    text.add_argument("--streams", type=int, default=4, help="partitions worked on at once")
    text.add_argument("--via", choices=["auto", "archive", "pdf"], default="auto",
                      help="stream each year's tar archive, fetch PDFs one by one, or pick by "
                           "how many are left (default)")
    text.add_argument("--retry-errors", action="store_true",
                      help="also redo judgments whose PDF could not be parsed before")

    st = sub.add_parser("structure", help="re-derive parties, acts, citations and summary "
                                          "from stored text (no downloads)")
    st.add_argument("--limit", type=int)
    st.add_argument("--all", action="store_true", help="redo rows already at the current version")
    st.add_argument("--workers", type=int, help="processes (default: one per CPU)")

    sub.add_parser("citations", help="link each judgment to the earlier judgments of this court it "
                                     "cites (\"cited by\"); `structure` and `update` run it too")
    sub.add_parser("labels", help="label how each judgment treats the ones it cites (followed, "
                                  "distinguished, doubted) without relinking; `citations` runs it too")
    sub.add_parser("goodlaw", help="flag judgments a later judgment set aside, recalled or overruled "
                                   "(run after `citations`); `structure` and `update` run it too")

    hi = sub.add_parser("hindi", help="convert Hindi orders typed in the old Kruti Dev font to "
                                      "readable, searchable Hindi (no downloads; safe to re-run)")
    hi.add_argument("--workers", type=int, help="processes (default: one per CPU)")

    up = sub.add_parser("update", help="import only what changed in the dataset since the last "
                                       "run, then extract text for the new judgments (run daily)")
    up.add_argument("--year", type=int, action="append", help="repeatable; default all years")
    up.add_argument("--text-limit", type=int, default=1000,
                    help="PDFs to fetch for judgments without text afterwards, newest first (0 = skip)")
    up.add_argument("--stale-days", type=int, default=21,
                    help="warn when the dataset itself hasn't changed for this many days")
    up.add_argument("--dry-run", action="store_true", help="only list the partitions that changed")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.command == "metadata":
        n = import_metadata(args.year, args.bench)
        print(f"imported {n} judgments")
    elif args.command == "supreme":
        n = import_supreme(args.year)
        print(f"imported {n} Supreme Court judgments")
    elif args.command == "update":
        from .update import run

        s = run(args.year, args.text_limit, args.stale_days, args.dry_run)
        changed = ", ".join(s["partitions"]) or "none"
        print(f"checked {s['checked']} partitions, changed: {changed}")
        if not args.dry_run:
            print(f"{s['new']} new judgments, {s['updated']} refreshed, text extracted for {s['text']}, "
                  f"{s['citations']} citations linked, {s['treatments']} judgments flagged as set aside or overruled; "
                  f"latest decision date {s['latest_decision']} (Supreme Court: {s['sc_latest_decision']})")
        if s["stale"]:
            print(f"warning: the dataset was last updated {s['dataset_updated']:%Y-%m-%d}, "
                  f"over {args.stale_days} days ago", file=sys.stderr)
    elif args.command == "citations":
        print(f"linked {link_citations()} citations")
    elif args.command == "labels":
        with db.connect() as conn:
            db.init_schema(conn)
            with conn.transaction():
                n = treatment.label_citations(conn)
        print(f"labelled {n} citations as followed, distinguished or doubted")
    elif args.command == "goodlaw":
        print(f"flagged {link_treatments()} judgments set aside, recalled or overruled")
    elif args.command == "text":
        from .text import extract_text

        s = extract_text(args.limit, args.year, args.workers, args.streams, args.via, args.retry_errors)
        print(f"extracted text for {s['done']} judgments in {s['seconds']}s ({s['per_second']}/s); "
              f"{s['errors']} unreadable, {s['failed']} could not be downloaded (a re-run retries them)")
    elif args.command == "hindi":
        n = hindi(args.workers)
        print(f"converted {n} Hindi judgments")
    else:
        n = structure(args.limit, args.all, args.workers)
        print(f"structured {n} judgments; linked {link_citations()} citations; "
              f"flagged {link_treatments()} judgments set aside, recalled or overruled")


if __name__ == "__main__":
    main(sys.argv[1:])
