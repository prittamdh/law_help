"""Import Rajasthan High Court judgments from the public AWS open-data bucket.

Dataset: https://registry.opendata.aws/indian-high-court-judgments/ (CC-BY-4.0).
Layout per year and bench:
    metadata/parquet/year=YYYY/court=8_9/bench=<bench>/metadata.parquet
    data/pdf/year=YYYY/court=8_9/bench=<bench>/<file>.pdf
"""

import argparse
import io
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pyarrow.parquet as pq

from . import db, extract
from .parse import BENCHES, bench_strength, parse_date, parse_judges, parse_title

BUCKET_URL = "https://indian-high-court-judgments.s3.ap-south-1.amazonaws.com"
COURT_CODE = "8_9"
COURT_NAME = "Rajasthan High Court"
SOURCE = "aws-hc-judgments"
CACHE_DIR = Path(".cache")

log = logging.getLogger("law_help.importer")

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


STRUCTURE_SQL = """
UPDATE judgments SET
    text_language = %(language)s, neutral_citation = %(neutral_citation)s,
    parties = %(parties)s, advocates = %(advocates)s, bench_judges = %(judges)s,
    acts_cited = %(acts_cited)s, cases_cited = %(cases_cited)s, summary = %(summary)s,
    extractor_version = %(extractor_version)s, structured_at = now()
WHERE id = %(id)s
"""


def _structure_params(judgment_id: int, text: str | None) -> dict:
    from psycopg.types.json import Jsonb

    fields = extract.extract(text or "")
    return {
        "id": judgment_id,
        "language": fields["language"],
        "neutral_citation": fields["neutral_citation"],
        "parties": Jsonb({"petitioners": fields["petitioners"], "respondents": fields["respondents"]}),
        "advocates": Jsonb(fields["advocates"]),
        "judges": fields["judges"],
        "acts_cited": Jsonb(fields["acts_cited"]),
        "cases_cited": Jsonb(fields["cases_cited"]),
        "summary": fields["summary"],
        "extractor_version": fields["extractor_version"],
    }


def extract_text(limit: int) -> int:
    """Download PDFs for judgments that have no text yet, store the text and its structure."""
    done = 0
    with httpx.Client(timeout=120) as client, db.connect() as conn:
        rows = conn.execute(
            "SELECT id, pdf_key FROM judgments WHERE text_extracted_at IS NULL "
            "ORDER BY decision_date DESC NULLS LAST LIMIT %s", (limit,),
        ).fetchall()
        for row in rows:
            text = None
            try:
                r = client.get(f"{BUCKET_URL}/{row['pdf_key']}")
                r.raise_for_status()
                text = extract.pdf_text(r.content)
            except Exception as exc:  # a bad PDF should not stop the batch
                log.warning("could not extract %s: %s", row["pdf_key"], exc)
            conn.execute(
                "UPDATE judgments SET full_text = %s, text_extracted_at = %s WHERE id = %s",
                (text, datetime.now(timezone.utc), row["id"]),
            )
            conn.execute(STRUCTURE_SQL, _structure_params(row["id"], text))
            conn.commit()
            done += 1
    return done


def structure(limit: int | None, redo: bool) -> int:
    """Re-derive structured fields from stored text; no downloads.

    By default only rows never structured, or structured by an older extractor version.
    """
    done = 0
    with db.connect() as conn:
        db.init_schema(conn)
        where = "full_text IS NOT NULL"
        if not redo:
            where += " AND (extractor_version IS NULL OR extractor_version < %(v)s)"
        rows = conn.execute(
            f"SELECT id, full_text FROM judgments WHERE {where} ORDER BY id "
            + ("LIMIT %(limit)s" if limit else ""),
            {"v": extract.EXTRACTOR_VERSION, "limit": limit},
        ).fetchall()
        for row in rows:
            conn.execute(STRUCTURE_SQL, _structure_params(row["id"], row["full_text"]))
            done += 1
            if done % 500 == 0:
                conn.commit()
        conn.commit()
    return done


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m law_help.importer")
    sub = parser.add_subparsers(dest="command", required=True)

    meta = sub.add_parser("metadata", help="import judgment metadata")
    meta.add_argument("--year", type=int, action="append", help="repeatable; default all years")
    meta.add_argument("--bench", choices=sorted(set(BENCHES.values())), action="append",
                      help="repeatable; default both benches")

    text = sub.add_parser("text", help="download PDFs, extract full text and structure")
    text.add_argument("--limit", type=int, default=100)

    st = sub.add_parser("structure", help="re-derive parties, acts, citations and summary "
                                          "from stored text (no downloads)")
    st.add_argument("--limit", type=int)
    st.add_argument("--all", action="store_true", help="redo rows already at the current version")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.command == "metadata":
        n = import_metadata(args.year, args.bench)
        print(f"imported {n} judgments")
    elif args.command == "text":
        n = extract_text(args.limit)
        print(f"extracted text for {n} judgments")
    else:
        n = structure(args.limit, args.all)
        print(f"structured {n} judgments")


if __name__ == "__main__":
    main(sys.argv[1:])
