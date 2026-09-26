"""Supreme Court judgments: metadata, reading the report layout, import, text, update and search.

The database tests use TEST_DATABASE_URL and skip when it is unreachable, like test_api.py.
"""

import io
import os
from datetime import date

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from law_help import db, extract, importer, supreme, text, update
from law_help.importer import SC_UPSERT_SQL, UPSERT_SQL

from test_text import make_pdf, make_tar

TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://law:law@localhost:5432/law_help_test")

CARD = (
    "<select>...</select><button onclick=open_pdf('10','2024','2024_10_108_125','2024INSC735');>"
    "<strong>VIJAY SINGH<span> versus </span>THE STATE OF BIHAR</strong></button></font><br>"
    "<strong>Coram : BELA M. TRIVEDI<sup class=\"tooltip-sup\" data-tooltip=\"Author\">*</sup>, "
    "SATISH CHANDRA SHARMA</strong><br> Issue for Consideration Whether the High Court could reverse "
    "an acquittal &ndash; ss. 302/34 IPC &ndash; Inter&shy; ference<br><strong class='caseDetailsTD' >"
    "<span> Decision Date :</span><font color='green'> 25-09-2024</font><span> | Case No :</span>"
    "<font color='green'> CRIMINAL APPEAL No. 1031/2015</font><span> | Disposal Nature :</span>"
    "<font color='green'> Disposed off</font> <span > |  Bench :</span><font color='green'> 3 Judges</font></strong>"
)

REPORT = [
    "[2024] 10 S.C.R. 108 : 2024 INSC 735",
    "Vijay Singh @ Vijay Kr. Sharma",
    "v.",
    "The State of Bihar",
    "(Criminal Appeal No. 1031 of 2015)",
    "25 September 2024",
    "[Bela M. Trivedi and Satish Chandra Sharma,* JJ.]",
    "Issue for Consideration",
    "Whether the High Court was right to reverse the acquittal of the appellants.",
    "Headnotes",
    "Penal Code, 1860 - ss. 302/34 and 364/34 - Circumstantial evidence - Chain incomplete.",
    "[2024] 10 S.C.R. 109",
    "Vijay Singh @ Vijay Kr. Sharma v. The State of Bihar",
    "Case Law Cited",
    "State of Goa v. Sanjay Thakran, (2007) 3 SCC 755 - relied on.",
    "Judgment",
    "1. The appellants were convicted under Section 302 of the Indian Penal Code, 1860.",
    "2. The appeals are allowed and the appellants are acquitted.",
    "Result of the Case: Appeals allowed.",
]

# An older volume: margin letters, capitals, no citation line or result line.
OLD_REPORT = [
    "A VASHISHT NARAIN KARWARIA", "v.", "STATE OF U.P. AND ANR.", "MARCH 28, 1990",
    "B [S. RATNAVEL PANDIAN AND", "c", "K. JAYACHANDRA REDDY, JJ.]",
    "National Security Act, 1980: Section 3(3) - Preventive detention.",
    "The petitioner was detained under section 3(3) of the National Security Act, 1980.",
    "V.N. KARWARIA v. STATE OF U.P. [PANDIAN, J.] 219",
    "We allow this appeal and quash the detention order.",
    "T.N.A. Appeal allowed and Petition disposed of.",
]


def parquet(*rows: dict) -> bytes:
    base = {"title": "VIJAY SINGH versus THE STATE OF BIHAR", "petitioner": "VIJAY SINGH",
            "respondent": "THE STATE OF BIHAR", "judge": "BELA M. TRIVEDI", "citation": "[2024] 10 S.C.R. 108",
            "case_id": "2024 INSC 735", "cnr": "ESCR010004822024", "decision_date": "25-09-2024",
            "disposal_nature": "Disposed off", "raw_html": CARD, "path": "2024_10_108_125"}
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist([{**base, **r} for r in rows]), buf)
    return buf.getvalue()


# --------------------------------------------------------------------------- no database


def test_parse_card():
    card = supreme.parse_card(CARD)
    assert card["judges"] == ["BELA M. TRIVEDI", "SATISH CHANDRA SHARMA"]
    assert (card["case_type"], card["case_number"], card["case_year"]) == ("CRIMINAL APPEAL", 1031, 2015)
    assert card["bench_size"] == 3
    assert card["headnote"] == ("Issue for Consideration Whether the High Court could reverse an acquittal "
                                "– ss. 302/34 IPC – Interference")
    assert supreme.parse_card(None)["judges"] == []


def test_to_records():
    (r,) = supreme.to_records(parquet({}, {"path": None}), 2024)
    assert r["source"] == "aws-sc-judgments" and r["court"] == "Supreme Court of India"
    assert r["pdf_key"] == r["pdf_link"] == "data/pdf/year=2024/english/2024_10_108_125_EN.pdf"
    assert r["title"] == "VIJAY SINGH Vs THE STATE OF BIHAR"
    assert r["bench_strength"] == "3-judge" and r["disposal_nature"] == "DISPOSED OFF"
    assert (r["neutral_citation"], r["report_citation"]) == ("2024 INSC 735", "[2024] 10 S.C.R. 108")
    assert r["decision_date"] == date(2024, 9, 25)


def test_extract_reads_a_supreme_court_report():
    f = extract.extract("\n".join(REPORT), supreme=True)
    assert f["neutral_citation"] == "2024 INSC 735"
    assert f["petitioners"] == ["Vijay Singh @ Vijay Kr. Sharma"] and f["respondents"] == ["The State of Bihar"]
    assert f["cases"] == ["Criminal Appeal No. 1031 of 2015"] and f["order_date"] == "2024-09-25"
    assert f["judges"] == ["BELA M. TRIVEDI", "SATISH CHANDRA SHARMA"]
    assert f["summary"] == "Whether the High Court was right to reverse the acquittal of the appellants. Appeals allowed."
    assert f["outcome"] == "Allowed"
    assert {"act": "Indian Penal Code, 1860", "sections": ["302"]} in f["acts_cited"]
    assert [c["name"] for c in f["cases_cited"]] == ["State of Goa v. Sanjay Thakran"]  # not its own running head
    assert f["case_refs"] == []


def test_extract_reads_an_old_report():
    f = extract.extract("\n".join(OLD_REPORT), supreme=True)
    assert f["petitioners"] == ["VASHISHT NARAIN KARWARIA"] and f["respondents"] == ["STATE OF U.P. AND ANR."]
    assert f["judges"] == ["S. RATNAVEL PANDIAN", "K. JAYACHANDRA REDDY"]
    assert f["order_date"] == "1990-03-28" and f["outcome"] == "Allowed"
    assert f["neutral_citation"] is None


def test_court_headline_and_url():
    assert extract.headline("CRIMINAL APPEAL", [], "Allowed") == "Criminal appeal · Allowed"
    assert importer.pdf_url("aws-sc-judgments", "data/pdf/x.pdf").startswith(supreme.BUCKET_URL)
    assert importer.pdf_url("aws-hc-judgments", "data/pdf/x.pdf").startswith(importer.BUCKET_URL)


def test_cli_parses_supreme():
    with pytest.raises(SystemExit):
        importer.main(["supreme", "--help"])


# --------------------------------------------------------------------------- database


@pytest.fixture
def conn(monkeypatch, tmp_path):
    try:
        c = db.connect(TEST_URL)
    except Exception:
        pytest.skip("no test database available")
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    monkeypatch.setattr(update, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(text.time, "sleep", lambda s: None)
    db.init_schema(c)
    c.execute("TRUNCATE judgments, source_partitions, text_archives, citations")
    c.commit()
    yield c
    c.close()


HC_ROW = {
    "source": "aws-hc-judgments", "court": "Rajasthan High Court", "bench": "jaipur", "cnr": "RJ1",
    "pdf_link": "court/j1.pdf", "pdf_key": "data/pdf/year=2024/court=8_9/bench=jaipur/j1.pdf",
    "case_type": "CRLA", "case_number": 1, "case_year": 2024, "title": "CRLA/1/2024 of A Vs STATE",
    "petitioner": "A", "respondent": "STATE", "judges": ["X"], "bench_strength": "single",
    "disposal_nature": "ALLOWED", "date_of_registration": None, "decision_date": "2024-05-01",
    "description": "acquittal murder",
}


class SCBucket:
    """The Supreme Court bucket: one metadata file per year, an English tar and single PDFs."""

    def __init__(self, etag="v1", body=b"", tars=None, pdfs=None):
        self.etag, self.body, self.tars, self.pdfs = etag, body, tars or {}, pdfs or {}
        self.hosts = set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.hosts.add(request.url.host)
        path = request.url.path.lstrip("/")
        if not path:
            prefix = request.url.params["prefix"]
            key = supreme.metadata_key(2024)
            items = {key: (self.etag, len(self.body))} if request.url.host in supreme.BUCKET_URL else {}
            items.update({k: (f"e-{len(v)}", len(v)) for k, v in self.tars.items()})
            listed = "".join(
                f"<Contents><Key>{k}</Key><LastModified>2026-09-15T10:33:04.000Z</LastModified>"
                f"<ETag>&quot;{e}&quot;</ETag><Size>{n}</Size></Contents>"
                for k, (e, n) in items.items() if k.startswith(prefix))
            return httpx.Response(200, text=f"<ListBucketResult>{listed}</ListBucketResult>")
        if path in self.tars:
            start = int(request.headers["range"].split("=")[1].rstrip("-"))
            return httpx.Response(206, content=self.tars[path][start:])
        if path in self.pdfs:
            return httpx.Response(200, content=self.pdfs[path])
        if path == supreme.metadata_key(2024):
            return httpx.Response(200, content=self.body)
        return httpx.Response(404)


def test_update_imports_the_supreme_court_and_skips_it_when_unchanged(conn):
    bucket = SCBucket(body=parquet({}, {"path": "2024_1_1_9", "cnr": "ESCR2", "case_id": "2024 INSC 9"}))
    with httpx.Client(transport=httpx.MockTransport(bucket.handler)) as client:
        first = update.update(client, conn, [2024], court="supreme")
        assert (first["changed"], first["new"], first["partitions"]) == (1, 2, ["2024/supreme"])
        assert update.update(client, conn, [2024], court="supreme")["changed"] == 0
        assert update.update(client, conn, [2024])["checked"] == 0   # the High Court listing is separate
    row = conn.execute("SELECT * FROM judgments WHERE cnr = 'ESCR2'").fetchone()
    assert (row["neutral_citation"], row["report_citation"]) == ("2024 INSC 9", "[2024] 10 S.C.R. 108")
    assert row["judges"] == ["BELA M. TRIVEDI", "SATISH CHANDRA SHARMA"]


def test_text_streams_the_english_archive_from_the_supreme_court_bucket(conn):
    key = "data/pdf/year=2024/english/"
    with conn.cursor() as cur:
        cur.executemany(SC_UPSERT_SQL, supreme.to_records(parquet({}, {"path": "2024_1_1_9", "cnr": "ESCR2"}), 2024))
    conn.commit()
    conn.autocommit = True
    bucket = SCBucket(tars={"data/tar/year=2024/english/english.tar": make_tar(
        {"2024_10_108_125_EN.pdf": make_pdf(*REPORT[:9])})}, pdfs={f"{key}2024_1_1_9_EN.pdf": make_pdf(*REPORT)})
    with httpx.Client(transport=httpx.MockTransport(bucket.handler)) as client:
        out = text.extract_text(workers=0, client=client, via="archive")
    assert (out["done"], out["errors"], out["failed"]) == (2, 0, 0)
    assert bucket.hosts == {httpx.URL(supreme.BUCKET_URL).host}
    got = {r["cnr"]: r for r in conn.execute("SELECT * FROM judgments")}
    assert got["ESCR2"]["outcome"] == "Allowed"
    assert got["ESCR010004822024"]["bench_judges"] == ["BELA M. TRIVEDI", "SATISH CHANDRA SHARMA"]
    # The citation from the metadata stays, even when the text doesn't print one.
    importer.structure(None, True, workers=0)
    assert conn.execute("SELECT neutral_citation FROM judgments WHERE cnr = 'ESCR2'").fetchone()[
        "neutral_citation"] == "2024 INSC 735"


def test_search_filters_by_court(conn):
    with conn.cursor() as cur:
        cur.execute(UPSERT_SQL, HC_ROW)
        cur.executemany(SC_UPSERT_SQL, supreme.to_records(parquet({}), 2024))
    conn.commit()
    from law_help.api import app

    client = TestClient(app)
    sc = client.get("/judgments", params={"court": "supreme"}).json()
    assert sc["total"] == 1
    (r,) = sc["results"]
    assert r["court"] == "Supreme Court of India" and r["report_citation"] == "[2024] 10 S.C.R. 108"
    assert r["pdf_url"] == f"{supreme.BUCKET_URL}/data/pdf/year=2024/english/2024_10_108_125_EN.pdf"
    hc = client.get("/judgments", params={"court": "rajasthan"}).json()
    assert [x["cnr"] for x in hc["results"]] == ["RJ1"]
    assert hc["results"][0]["pdf_url"].startswith(importer.BUCKET_URL)
    assert client.get("/judgments", params={"q": "acquittal"}).json()["total"] == 2  # SC headnote is searched
    assert client.get("/judgments", params={"court": "delhi"}).status_code == 422
    assert client.get(f"/judgments/{r['id']}").json()["citation"] == (
        "Vijay Singh v. The State of Bihar, 2024 INSC 735 : [2024] 10 S.C.R. 108 "
        "[Criminal Appeal No. 1031 of 2015, decided on 25.09.2024]")
    assert {c["court"] for c in client.get("/stats").json()["by_court"]} == {
        "Supreme Court of India", "Rajasthan High Court"}
