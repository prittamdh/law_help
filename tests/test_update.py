"""`importer update` against a fake bucket and the test database. Skipped when the DB is unreachable."""

import io
import os
from datetime import datetime

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from law_help import db, importer, update

TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://law:law@localhost:5432/law_help_test")
KEY = "metadata/parquet/year=2026/court=8_9/bench=jaipur/metadata.parquet"


def parquet(*cnrs: str) -> bytes:
    rows = [{
        "title": f"CW/{i}/2026 of RAM Vs STATE", "description": "Writ petition", "judge": "HON'BLE MR. JUSTICE A B",
        "pdf_link": f"court/cnrorders/jaipur/orders/{cnr}_1_2026-09-01.pdf", "cnr": cnr,
        "date_of_registration": "02-01-2026", "decision_date": datetime(2026, 9, i + 1), "disposal_nature": "DISMISSED",
    } for i, cnr in enumerate(cnrs)]
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows), buf)
    return buf.getvalue()


class Bucket:
    """Serves one partition; `publish` swaps its content and ETag like a dataset refresh."""

    def __init__(self):
        self.etag, self.body, self.downloads = None, b"", 0

    def publish(self, etag: str, body: bytes):
        self.etag, self.body = etag, body

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            prefix = request.url.params["prefix"]
            listed = KEY.startswith(prefix) and self.etag
            contents = (f"<Contents><Key>{KEY}</Key><LastModified>2026-09-15T10:33:04.000Z</LastModified>"
                        f"<ETag>&quot;{self.etag}&quot;</ETag><Size>{len(self.body)}</Size></Contents>") if listed else ""
            return httpx.Response(200, text=f"<ListBucketResult>{contents}</ListBucketResult>")
        self.downloads += 1
        return httpx.Response(200, content=self.body)


@pytest.fixture
def env(tmp_path, monkeypatch):
    try:
        conn = db.connect(TEST_URL)
    except Exception:
        pytest.skip("no test database available")
    monkeypatch.setattr(update, "CACHE_DIR", tmp_path)
    db.init_schema(conn)
    conn.execute("TRUNCATE judgments, source_partitions")
    conn.commit()
    bucket = Bucket()
    with httpx.Client(transport=httpx.MockTransport(bucket.handler)) as client:
        yield bucket, client, conn
    conn.close()


def test_only_changed_partitions_are_imported(env):
    bucket, client, conn = env
    bucket.publish("v1", parquet("RJHC1", "RJHC2"))
    first = update.update(client, conn, [2025, 2026])
    assert (first["checked"], first["changed"], first["new"]) == (1, 1, 2)
    assert str(first["latest_decision"]) == "2026-09-02"

    again = update.update(client, conn, [2025, 2026])
    assert (again["changed"], again["new"], bucket.downloads) == (0, 0, 1)

    bucket.publish("v2", parquet("RJHC1", "RJHC2", "RJHC3"))
    assert update.update(client, conn, [2026], dry_run=True)["partitions"] == ["2026/jaipur"]
    third = update.update(client, conn, [2026])
    assert (third["new"], third["updated"]) == (1, 2)
    assert conn.execute("SELECT count(*) AS n FROM judgments").fetchone()["n"] == 3


def test_extracted_text_survives_a_refresh(env):
    bucket, client, conn = env
    bucket.publish("v1", parquet("RJHC1"))
    update.update(client, conn, [2026])
    conn.execute("UPDATE judgments SET full_text = 'kept', text_extracted_at = now()")
    conn.commit()
    bucket.publish("v2", parquet("RJHC1", "RJHC2"))
    update.update(client, conn, [2026])
    rows = conn.execute("SELECT cnr, full_text FROM judgments ORDER BY cnr").fetchall()
    assert [(r["cnr"], r["full_text"]) for r in rows] == [("RJHC1", "kept"), ("RJHC2", None)]


def test_cli_parses_update():
    with pytest.raises(SystemExit):
        importer.main(["update", "--help"])
