"""Supreme Court of India judgments from the public AWS open-data bucket.

Dataset: https://registry.opendata.aws/indian-supreme-court-judgments/ (CC-BY-4.0), built
from the Supreme Court's eSCR portal: every reported judgment since 1950, about 43,500.
Layout per year (one partition per year, no benches):
    metadata/parquet/year=YYYY/metadata.parquet
    data/pdf/year=YYYY/english/<path>_EN.pdf        (translations sit beside it; we take English)
    data/tar/year=YYYY/english/english.tar (+ part-*.tar for later additions)

The PDFs are the Supreme Court Reports pages, with the report's own headnote.
"""

import html
import io
import re

import pyarrow.parquet as pq

from .parse import parse_date

BUCKET_URL = "https://indian-supreme-court-judgments.s3.ap-south-1.amazonaws.com"
SOURCE = "aws-sc-judgments"
COURT_NAME = "Supreme Court of India"
BENCH = "supreme court"  # judgments.bench is NOT NULL; the Supreme Court has one seat
FIRST_YEAR = 1950


def metadata_key(year: int) -> str:
    return f"metadata/parquet/year={year}/metadata.parquet"


def pdf_key(year: int, path: str) -> str:
    return f"data/pdf/year={year}/english/{path}_EN.pdf"


def archive_prefix(year: int) -> str:
    return f"data/tar/year={year}/english/"


# raw_html is the eSCR search result card: "Coram : A*, B</strong><br> headnote<br><strong ...>
# Decision Date : ... | Case No : CRIMINAL APPEAL No. 1031/2015 | ... | Bench : 2 Judges"
_CORAM_RE = re.compile(r"Coram\s*:\s*(.*?)</strong>\s*(?:<br>)?(.*?)<br>\s*<strong class='caseDetailsTD'", re.S)
_CASE_NO_RE = re.compile(r"Case No\s*:\s*</span>\s*<font[^>]*>\s*([^<]+?)\s*</font>")
_BENCH_RE = re.compile(r"Bench\s*:\s*</span>\s*<font[^>]*>\s*(\d+)\s*Judges?", re.I)
_CASE_PARTS_RE = re.compile(r"^(?P<type>.*?)\s+No\.?\s*(?P<number>\d+)\s*(?:/|of)\s*(?P<year>\d{4})", re.I)


def _text(fragment: str) -> str:
    fragment = re.sub(r"<sup.*?</sup>", "", fragment, flags=re.S)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    fragment = re.sub(r"­\s*", "", html.unescape(fragment))  # a soft hyphen breaking a word
    return re.sub(r"\s+", " ", fragment).strip()


def parse_card(raw_html: str | None) -> dict:
    """Judges, case number, bench size and the headnote blurb from one search-result card."""
    out = {"judges": [], "case_type": None, "case_number": None, "case_year": None,
           "bench_size": None, "headnote": None}
    raw = raw_html or ""
    m = _CORAM_RE.search(raw)
    if m:
        out["judges"] = [j.strip(" *.").upper() for j in _text(m[1]).split(",") if j.strip(" *.")]
        out["headnote"] = _text(m[2]) or None
    m = _CASE_NO_RE.search(raw)
    if m:
        parts = _CASE_PARTS_RE.match(_text(m[1]))
        if parts:
            out["case_type"] = re.sub(r"\s+", " ", parts["type"]).strip().upper() or None
            out["case_number"] = int(parts["number"])
            out["case_year"] = int(parts["year"])
    m = _BENCH_RE.search(raw)
    if m:
        out["bench_size"] = int(m[1])
    return out


def to_records(parquet_bytes: bytes, year: int) -> list[dict]:
    """One year's metadata as rows for importer.UPSERT_SQL."""
    table = pq.read_table(io.BytesIO(parquet_bytes), columns=[
        "title", "petitioner", "respondent", "judge", "citation", "case_id", "cnr",
        "decision_date", "disposal_nature", "raw_html", "path",
    ])
    records = []
    for row in table.to_pylist():
        if not row["path"]:
            continue
        card = parse_card(row["raw_html"])
        judges = card["judges"] or [j.strip().upper() for j in (row["judge"] or "").split(",") if j.strip()]
        size = card["bench_size"] or len(judges) or None
        key = pdf_key(year, row["path"])
        records.append({
            "source": SOURCE,
            "court": COURT_NAME,
            "bench": BENCH,
            "cnr": row["cnr"],
            "pdf_link": key,
            "pdf_key": key,
            "case_type": card["case_type"],
            "case_number": card["case_number"],
            "case_year": card["case_year"],
            "title": re.sub(r"\s+versus\s+", " Vs ", (row["title"] or "").strip(), flags=re.I),
            "petitioner": (row["petitioner"] or "").strip() or None,
            "respondent": (row["respondent"] or "").strip() or None,
            "judges": judges,
            "bench_strength": f"{size}-judge" if size else None,
            # upper case like the High Court's, so one outcome filter serves both
            "disposal_nature": (row["disposal_nature"] or "").strip().upper() or None,
            "date_of_registration": None,
            "decision_date": parse_date(row["decision_date"]),
            "description": card["headnote"],
            "neutral_citation": (row["case_id"] or "").strip() or None,
            "report_citation": (row["citation"] or "").strip() or None,
        })
    return records
