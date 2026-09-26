"""`importer text` against a fake bucket and the test database. Skipped when the DB is unreachable."""

import io
import os
import tarfile
from concurrent.futures import Future

import httpx
import pytest

from law_help import db, extract, text
from law_help.importer import UPSERT_SQL

TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://law:law@localhost:5432/law_help_test")
PDF_DIR = "data/pdf/year=2026/court=8_9/bench=jaipur/"
TAR_DIR = "data/tar/year=2026/court=8_9/bench=jaipur/"


def make_pdf(*lines: str) -> bytes:
    """A one-page PDF with a real text layer."""
    stream = "BT /F1 11 Tf 50 780 Td 14 TL " + " ".join(
        "(" + line.replace("(", r"\(").replace(")", r"\)") + ") '" for line in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{obj}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def judgment_pdf(n: int) -> bytes:
    return make_pdf(f"[2026:RJ-JP:{n}]", "HIGH COURT OF JUDICATURE FOR RAJASTHAN",
                    f"S.B. Criminal Misc Bail Application No. {n}/2026", "Ram Lal", "----Petitioner",
                    "Versus", "State Of Rajasthan", "----Respondent", "HON'BLE MR. JUSTICE A B",
                    "Order", "01/09/2026", "1. This bail application under Section 439 Cr.P.C. has been filed",
                    "on behalf of the petitioner, who is in custody since his arrest in the case.",
                    "2. Heard learned counsel for the parties and perused the material on record.",
                    "3. Accordingly, the bail application is allowed.")


def make_tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class Bucket:
    """Serves tar archives (with Range) and single PDFs. `drop_after` cuts the first
    archive response after that many bytes, like a connection reset."""

    def __init__(self, tars: dict[str, bytes], pdfs: dict[str, bytes]):
        self.tars, self.pdfs = tars, pdfs
        self.drop_after = None
        self.ranges, self.pdf_gets = [], []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.lstrip("/")
        if not path:
            prefix = request.url.params["prefix"]
            listed = "".join(
                f"<Contents><Key>{k}</Key><ETag>&quot;e-{len(v)}&quot;</ETag><Size>{len(v)}</Size></Contents>"
                for k, v in self.tars.items() if k.startswith(prefix))
            return httpx.Response(200, text=f"<ListBucketResult>{listed}</ListBucketResult>")
        if path in self.tars:
            start = int(request.headers["range"].split("=")[1].rstrip("-"))
            self.ranges.append((path, start))
            body = self.tars[path][start:]
            if self.drop_after is not None:
                cut, self.drop_after = self.drop_after, None
                return httpx.Response(206, stream=_Dropping(body[:cut]))
            return httpx.Response(206, content=body)
        self.pdf_gets.append(path)
        if path in self.pdfs:
            return httpx.Response(200, content=self.pdfs[path])
        return httpx.Response(404)


class _Dropping(httpx.SyncByteStream):
    def __init__(self, data: bytes):
        self.data = data

    def __iter__(self):
        yield self.data
        raise httpx.ReadError("connection reset")


@pytest.fixture
def conn(monkeypatch):
    try:
        c = db.connect(TEST_URL)
    except Exception:
        pytest.skip("no test database available")
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    monkeypatch.setattr(text, "BATCH", 2)
    monkeypatch.setattr(text, "PER_STREAM_INFLIGHT", 1)
    monkeypatch.setattr(text.time, "sleep", lambda s: None)
    db.init_schema(c)
    c.execute("TRUNCATE judgments, text_archives")
    for i in range(1, 7):
        c.execute(UPSERT_SQL, {
            "source": "test", "court": "Rajasthan High Court", "bench": "jaipur", "cnr": f"C{i}",
            "pdf_link": f"orders/J{i}.pdf", "pdf_key": f"{PDF_DIR}J{i}.pdf", "case_type": "SBCRLMB",
            "case_number": i, "case_year": 2026, "title": "t", "petitioner": None, "respondent": None,
            "judges": [], "bench_strength": None, "disposal_nature": None, "date_of_registration": None,
            "decision_date": f"2026-09-{i:02d}", "description": None,
        })
    c.commit()
    c.autocommit = True  # an open read would block the next run's schema check
    yield c
    c.close()


def bucket_for_six() -> Bucket:
    """J1-J4 in data.tar (J3 is not a PDF), J5 in a later part, J6 only as a single PDF."""
    data_tar = make_tar({"J1.pdf": judgment_pdf(1), "other.pdf": b"%PDF" + b"x" * 30000,
                         "J2.pdf": judgment_pdf(2), "J3.pdf": b"not a pdf", "J4.pdf": judgment_pdf(4)})
    part = make_tar({"J5.pdf": judgment_pdf(5)})
    return Bucket({f"{TAR_DIR}data.tar": data_tar, f"{TAR_DIR}part-20260901T000000Z.tar": part},
                  {f"{PDF_DIR}J6.pdf": judgment_pdf(6)})


def rows(conn):
    return {r["cnr"]: r for r in conn.execute("SELECT * FROM judgments ORDER BY cnr")}


def run(bucket, **kw):
    with httpx.Client(transport=httpx.MockTransport(bucket.handler)) as client:
        return text.extract_text(workers=0, client=client, **kw)


def test_backfill_streams_archives_then_fetches_the_rest(conn):
    bucket = bucket_for_six()
    out = run(bucket, via="archive")
    assert (out["done"], out["errors"], out["failed"]) == (6, 1, 0)

    got = rows(conn)
    assert got["C1"]["neutral_citation"] == "2026:RJ-JP:1"
    assert got["C1"]["text_language"] == "en" and got["C1"]["text_error"] is None
    assert got["C5"]["neutral_citation"] == "2026:RJ-JP:5"      # from the later part
    assert got["C6"]["neutral_citation"] == "2026:RJ-JP:6"      # fetched on its own
    assert got["C3"]["full_text"] is None and got["C3"]["text_error"]
    assert got["C3"]["text_language"] == "no-text"
    assert all(r["text_extracted_at"] for r in got.values())
    assert bucket.pdf_gets == [f"{PDF_DIR}J6.pdf"]
    assert conn.execute("SELECT bool_and(done) AS d FROM text_archives").fetchone()["d"]

    assert run(bucket)["done"] == 0                              # nothing left to do


def test_a_dropped_connection_resumes_with_a_range_request(conn):
    bucket = bucket_for_six()
    bucket.drop_after = 20000                                    # mid-way through other.pdf
    assert run(bucket, via="archive")["done"] == 6
    data_tar = [start for key, start in bucket.ranges if key.endswith("data.tar")]
    assert data_tar[0] == 0 and data_tar[1] > 0                  # not from the start again
    assert all(r["full_text"] or r["cnr"] == "C3" for r in rows(conn).values())


def test_a_new_run_resumes_from_the_saved_offset(conn):
    bucket = bucket_for_six()
    key = f"{TAR_DIR}data.tar"
    members = tarfile.open(fileobj=io.BytesIO(bucket.tars[key])).getmembers()
    j4 = next(m for m in members if m.name == "J4.pdf")
    # As if a run died after writing J1-J3: they are done and the offset points at J4.
    conn.execute("UPDATE judgments SET text_extracted_at = now() WHERE cnr IN ('C1', 'C2', 'C3')")
    conn.execute("INSERT INTO text_archives (key, etag, next_offset) VALUES (%s, %s, %s)",
                 (key, f"e-{len(bucket.tars[key])}", j4.offset))
    conn.commit()
    assert run(bucket, via="archive")["done"] == 3
    assert (key, j4.offset) in bucket.ranges and (key, 0) not in bucket.ranges
    assert rows(conn)["C4"]["neutral_citation"] == "2026:RJ-JP:4"


def test_limit_fetches_the_newest_single_pdfs(conn):
    bucket = bucket_for_six()
    bucket.pdfs.update({f"{PDF_DIR}J5.pdf": judgment_pdf(5)})
    out = run(bucket, limit=2)
    assert out["done"] == 2 and bucket.ranges == []
    assert sorted(bucket.pdf_gets) == [f"{PDF_DIR}J5.pdf", f"{PDF_DIR}J6.pdf"]


def test_a_missing_pdf_is_recorded_and_a_parse_error_can_be_retried(conn):
    bucket = bucket_for_six()                                    # J1-J5 aren't served one by one
    run(bucket, via="pdf")
    got = rows(conn)
    assert got["C1"]["text_error"] == "PDF not in the dataset"
    assert got["C6"]["text_error"] is None
    bucket.pdfs[f"{PDF_DIR}J1.pdf"] = judgment_pdf(1)
    assert run(bucket, via="pdf", retry_errors=True)["done"] == 5
    assert rows(conn)["C1"]["neutral_citation"] == "2026:RJ-JP:1"


def test_parse_pdf_reports_errors_instead_of_raising():
    out = text.parse_pdf(b"garbage")
    assert out["text"] is None and out["error"] and out["fields"]["language"] == "no-text"
    ok = text.parse_pdf(judgment_pdf(7))
    assert "HIGH COURT OF JUDICATURE" in ok["text"] and ok["error"] is None
    assert extract.neutral_citation(ok["text"]) == "2026:RJ-JP:7"


def test_a_structure_bug_keeps_the_text_and_records_the_error(monkeypatch):
    real = extract.extract

    def broken(text, supreme=False):
        if text:
            raise AttributeError("rule bug")
        return real(text, supreme)

    monkeypatch.setattr(extract, "extract", broken)
    out = text.parse_pdf(judgment_pdf(8))
    assert "HIGH COURT OF JUDICATURE" in out["text"]
    assert out["error"] == "structure: AttributeError: rule bug"


def test_a_broken_pool_reparses_each_pdf_on_its_own():
    """When a crash breaks the shared pool, the PDFs in flight are parsed again one by one,
    so they aren't blamed for the crash; the pool is replaced."""
    from concurrent.futures.process import BrokenProcessPool

    class Broken:
        _broken = "a worker died"

        def submit(self, *a):
            raise BrokenProcessPool(self._broken)

        def shutdown(self, **_):
            pass

    parser = text.Parser(0)
    parser._pool = Broken()
    failed = Future()
    failed.set_exception(BrokenProcessPool("a worker died"))
    out = parser.result(failed, judgment_pdf(9))
    assert out["error"] is None and extract.neutral_citation(out["text"]) == "2026:RJ-JP:9"
    assert not isinstance(parser._pool, Broken)
    assert parser.result(parser.submit(judgment_pdf(10)), judgment_pdf(10))["error"] is None
