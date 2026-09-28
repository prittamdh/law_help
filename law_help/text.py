"""Fetch judgment PDFs and extract their text and structure, in parallel and resumably.

PDFs come from the dataset in one of two ways:

* Archives. Each year and bench is also published as tar files under `data/tar/`
  (Jaipur 2024 is one 4 GB `data.tar` plus small `part-*.tar` files for later additions).
  Streaming one is a single request instead of 55,000, so a backfill is bound by
  bandwidth and CPU, not request latency. Several archives stream at once.
* Single PDFs. One GET per judgment on a thread pool. `update` uses this for the few
  hundred judgments a refresh brings, and a backfill uses it for partitions with little
  left to do and for any judgment its archive lacks.

Parsing (pdfium, then law_help.extract) is CPU bound, so it runs in a process pool.

Resuming: a judgment is done once text_extracted_at is set, and results are committed in
batches, so a killed run loses at most one batch per stream. For archives, `text_archives`
also keeps the byte offset of the first member not yet written, and the next run resumes
there with a Range request instead of re-reading the whole archive.

Failures: a PDF that can't be parsed is marked done with its error in `text_error` and
language 'no-text'. A PDF that can't be downloaded is left pending for the next run.
"""

import io
import logging
import multiprocessing
import os
import re
import tarfile
import threading
import time
from collections import deque
from concurrent.futures import CancelledError, Future, ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import PurePosixPath

import httpx
import psycopg

from . import db, extract
from . import supreme
from .importer import BUCKET_URL, COURT_CODE, STRUCTURE_SQL, _structure_params, bucket_url, is_supreme, pdf_url

log = logging.getLogger("law_help.text")

# A partition with at least this many judgments left streams its archive; fewer are
# fetched one PDF at a time (about 100 a second, against roughly 200 per archive stream).
ARCHIVE_MIN_PENDING = 2000
# A Supreme Court year's archive holds only its few hundred English judgments, so it pays sooner.
SC_ARCHIVE_MIN_PENDING = 100
BATCH = 100              # judgments per commit
DOWNLOADERS = 32         # concurrent single-PDF requests
PER_STREAM_INFLIGHT = 64 # PDFs read from an archive but not yet written
RETRIES = 4

TEXT_SQL = STRUCTURE_SQL.replace(
    "UPDATE judgments SET",
    "UPDATE judgments SET full_text = %(text)s, full_text_original = %(original)s, text_error = %(error)s, "
    "text_extracted_at = now(),",
)


# --------------------------------------------------------------------------- CPU work


def parse_pdf(data: bytes, sc: bool = False) -> dict:
    """Text, parse error (if any) and structured fields for one PDF. Runs in a worker.

    `sc`: a Supreme Court judgment, whose layout law_help.extract reads differently."""
    text, original, error = None, None, None
    try:
        text = extract.pdf_text(data)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:300]
    try:
        text, original = extract.readable(text)
        fields = extract.extract(text or "", sc)
    except Exception as exc:  # a rule bug must not cost the text, or stop the run
        error = f"structure: {type(exc).__name__}: {exc}"[:300]
        fields = extract.extract("")
    return {"text": text, "original": original, "error": error, "fields": fields}


def _row(judgment_id: int, parsed: dict) -> dict:
    params = _structure_params(judgment_id, parsed["text"], parsed["fields"])
    params.update(text=parsed["text"], original=parsed.get("original"), error=parsed["error"])
    return params


class _Inline:
    """Stands in for the process pool when workers=0 (tests, debugging)."""

    def submit(self, fn, *args) -> Future:
        f = Future()
        try:
            f.set_result(fn(*args))
        except Exception as exc:
            f.set_exception(exc)
        return f

    def shutdown(self, **_):
        pass


class Parser:
    """A process pool that survives a worker crash (a segfault in pdfium kills the pool).

    A crash breaks the whole pool, failing every PDF in flight with it. Each of those is then
    parsed again on its own in a one-off process, so only the PDF that really crashes is
    marked unreadable, and the shared pool is replaced once rather than once per stream.
    """

    def __init__(self, workers: int | None):
        self.workers = os.cpu_count() if workers is None else workers
        self._lock = threading.Lock()
        self._pool = self._new()

    def _new(self, workers: int | None = None):
        # forkserver, not fork: the parent has download threads running when the pool starts
        ctx = multiprocessing.get_context("forkserver")
        workers = self.workers if workers is None else workers
        return ProcessPoolExecutor(workers, mp_context=ctx) if workers else _Inline()

    def submit(self, data: bytes, sc: bool = False) -> Future:
        for _ in range(3):
            with self._lock:
                pool = self._pool
            try:
                return pool.submit(parse_pdf, data, sc)
            except (BrokenProcessPool, RuntimeError):  # RuntimeError: another thread shut it down
                self._replace_if_broken()
        return _done(self._alone(data, sc))

    def result(self, fut: Future, data: bytes, sc: bool = False) -> dict:
        try:
            return fut.result()
        except (BrokenProcessPool, CancelledError):
            self._replace_if_broken()
            return self._alone(data, sc)

    def _alone(self, data: bytes, sc: bool) -> dict:
        """Parse one PDF in a process of its own, so a crash costs only this PDF."""
        pool = self._new(1)
        try:
            return pool.submit(parse_pdf, data, sc).result()
        except BrokenProcessPool:
            return {"text": None, "error": "extractor crashed on this PDF", "fields": extract.extract("")}
        finally:
            pool.shutdown(wait=True)

    def _replace_if_broken(self):
        with self._lock:
            if getattr(self._pool, "_broken", False) or getattr(self._pool, "_shutdown_thread", False):
                log.warning("a parser process died; starting a new pool")
                self._pool.shutdown(wait=False, cancel_futures=True)
                self._pool = self._new()

    def shutdown(self):
        self._pool.shutdown(wait=True)


# --------------------------------------------------------------------------- bookkeeping


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.done = self.errors = self.failed = self.bytes = 0
        self.start = self._last_log = time.monotonic()

    def add(self, done=0, errors=0, failed=0, nbytes=0):
        with self.lock:
            self.done += done
            self.errors += errors
            self.failed += failed
            self.bytes += nbytes
            now = time.monotonic()
            if now - self._last_log >= 30:
                self._last_log = now
                elapsed = now - self.start
                log.info("%d judgments done (%.0f/s, %.1f MB/s), %d unreadable, %d download failures",
                         self.done, self.done / elapsed, self.bytes / elapsed / 1e6, self.errors, self.failed)


def _write(conn: psycopg.Connection, rows: list[dict], stats: Stats) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(TEXT_SQL, rows)
    stats.add(done=len(rows), errors=sum(1 for r in rows if r["error"]))


def _get(client: httpx.Client, url: str) -> httpx.Response | None:
    """GET with retries on network errors and 5xx. Returns None for a missing object."""
    for attempt in range(RETRIES):
        try:
            r = client.get(url)
            if r.status_code in (403, 404):
                return None
            r.raise_for_status()
            return r
        except (httpx.TransportError, httpx.HTTPStatusError):
            if attempt == RETRIES - 1:
                raise
            time.sleep(2 ** attempt)


# --------------------------------------------------------------------------- single PDFs


def fetch_pdfs(client: httpx.Client, parser: Parser, rows: list[dict], stats: Stats,
               downloaders: int = DOWNLOADERS) -> None:
    """Download and parse these rows (id, source, pdf_key) one PDF at a time, many at once."""

    def download(row):
        try:
            r = _get(client, pdf_url(row["source"], row["pdf_key"]))
        except Exception as exc:
            log.warning("could not download %s: %s", row["pdf_key"], exc)
            return row, None, "failed"
        if r is None:
            return row, None, "missing"
        return row, r.content, None

    with db.connect() as conn, ThreadPoolExecutor(downloaders) as dl:
        # Chunks keep memory bounded: at most one chunk of PDFs is held at a time.
        for i in range(0, len(rows), BATCH * 4):
            inflight = []
            for row, data, problem in dl.map(download, rows[i:i + BATCH * 4]):
                if problem == "failed":
                    stats.add(failed=1)
                elif problem == "missing":
                    parsed = {"text": None, "error": "PDF not in the dataset", "fields": extract.extract("")}
                    inflight.append((row, None, _done(parsed)))
                else:
                    stats.add(nbytes=len(data))
                    inflight.append((row, data, parser.submit(data, is_supreme(row["source"]))))
            out = [_row(row["id"], parser.result(fut, data, is_supreme(row["source"])))
                   for row, data, fut in inflight]
            for j in range(0, len(out), BATCH):
                _write(conn, out[j:j + BATCH], stats)
                conn.commit()


def _done(value) -> Future:
    f = Future()
    f.set_result(value)
    return f


# --------------------------------------------------------------------------- archives


class _HTTPStream(io.RawIOBase):
    """A streamed httpx response as a file, for tarfile's streaming mode."""

    def __init__(self, response: httpx.Response):
        self._chunks = response.iter_bytes()
        self._buf = memoryview(b"")

    def readable(self):
        return True

    def readinto(self, b):
        if not self._buf:
            self._buf = memoryview(next(self._chunks, b""))
        n = min(len(b), len(self._buf))
        b[:n] = self._buf[:n]
        self._buf = self._buf[n:]
        return n


def list_archives(client: httpx.Client, year: int, bench_code: str, source: str | None = None) -> list[dict]:
    """The tar files for one partition, the main one first, then the later parts in order.

    A High Court partition is a year and bench (data.tar); a Supreme Court one is a year of
    English judgments (english.tar)."""
    sc = source is not None and is_supreme(source)
    prefix = supreme.archive_prefix(year) if sc else f"data/tar/year={year}/court={COURT_CODE}/bench={bench_code}/"
    bucket = bucket_url(source) if source else BUCKET_URL
    r = client.get(bucket, params={"list-type": "2", "prefix": prefix})
    r.raise_for_status()
    found = []
    for block in re.findall(r"<Contents>(.*?)</Contents>", r.text, re.S):
        fields = dict(re.findall(r"<(Key|ETag|Size)>([^<]*)</", block))
        if fields.get("Key", "").endswith(".tar"):
            found.append({"key": fields["Key"], "etag": fields["ETag"].replace("&quot;", "").strip('"'),
                          "size": int(fields["Size"]), "url": f"{bucket}/{fields['Key']}", "sc": sc})
    return sorted(found, key=lambda a: (not a["key"].endswith(("/data.tar", "/english.tar")), a["key"]))


def stream_archive(client: httpx.Client, parser: Parser, archive: dict, pending: dict[str, int],
                   stats: Stats) -> None:
    """Parse every PDF in one tar whose name is in `pending` (file name -> judgment id).

    Found names are removed from `pending`, so whatever is left afterwards is not in this
    archive. Writes and the resume offset are committed together, batch by batch.
    """
    key, url, sc = archive["key"], archive["url"], archive.get("sc", False)
    with db.connect() as conn:
        saved = conn.execute("SELECT etag, next_offset, done FROM text_archives WHERE key = %s",
                             (key,)).fetchone()
        offset = saved["next_offset"] if saved and saved["etag"] == archive["etag"] else 0
        if saved and saved["etag"] == archive["etag"] and saved["done"]:
            offset = archive["size"]  # finished before; still look for rows added since
        if offset:
            log.info("%s: resuming at %.0f%%", key, 100 * offset / max(1, archive["size"]))

        inflight: deque = deque()  # (member offset, judgment id, pdf bytes, future), in tar order
        batch: list[dict] = []
        read_to = offset            # the next header to read if the connection drops

        def save(next_offset, done=False):
            _write(conn, batch, stats)
            batch.clear()
            conn.execute("""
                INSERT INTO text_archives (key, etag, next_offset, done, updated_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (key) DO UPDATE SET etag = EXCLUDED.etag,
                    next_offset = EXCLUDED.next_offset, done = EXCLUDED.done, updated_at = now()
            """, (key, archive["etag"], next_offset, done))
            conn.commit()

        def drain(keep: int):
            while len(inflight) > keep:
                _, jid, data, fut = inflight.popleft()
                batch.append(_row(jid, parser.result(fut, data, sc)))
                if len(batch) >= BATCH:
                    save(inflight[0][0] if inflight else read_to)

        attempts = 0
        while read_to < archive["size"] and pending:
            try:
                with client.stream("GET", url, headers={"Range": f"bytes={read_to}-"}) as r:
                    r.raise_for_status()
                    raw = _HTTPStream(r)
                    tar = tarfile.open(fileobj=io.BufferedReader(raw, 1 << 20), mode="r|")
                    base = read_to
                    for member in tar:
                        attempts = 0
                        name = PurePosixPath(member.name).name
                        if not member.isfile() or name not in pending:
                            read_to = base + tar.offset  # the next header; this member's data is skipped
                            continue
                        data = tar.extractfile(member).read()
                        start, read_to = base + member.offset, base + tar.offset
                        jid = pending.pop(name)
                        stats.add(nbytes=len(data))
                        inflight.append((start, jid, data, parser.submit(data, sc)))
                        drain(PER_STREAM_INFLIGHT)
                        if not pending:
                            break  # the rest of the archive has nothing we need
                    else:
                        read_to = archive["size"]
            except (httpx.TransportError, httpx.HTTPStatusError, tarfile.ReadError, EOFError) as exc:
                attempts += 1
                if attempts > RETRIES:
                    log.warning("%s: giving up at byte %d: %s", key, read_to, exc)
                    break
                log.info("%s: connection dropped at %.0f%% (%s), reconnecting",
                         key, 100 * read_to / archive["size"], exc)
                time.sleep(2 ** attempts)
        drain(0)
        save(read_to, done=read_to >= archive["size"])


# --------------------------------------------------------------------------- driver


# High Court: data/pdf/year=2024/court=8_9/bench=jaipur/; Supreme Court: data/pdf/year=2024/english/
_PARTITION_RE = re.compile(r"^data/pdf/year=(\d+)/(?:court=[^/]+/bench=([^/]+)|(english))/")


def pending_partitions(conn: psycopg.Connection, years: list[int] | None) -> list[dict]:
    rows = conn.execute("""
        SELECT source, substring(pdf_key from '^(.*/)[^/]+$') AS prefix, count(*) AS n
        FROM judgments WHERE text_extracted_at IS NULL GROUP BY 1, 2
    """).fetchall()
    out = []
    for r in rows:
        m = _PARTITION_RE.match(r["prefix"] or "")
        if m and (not years or int(m[1]) in years):
            out.append({"source": r["source"], "prefix": r["prefix"], "year": int(m[1]),
                        "bench": m[2] or "supreme", "pending": r["n"]})
    return sorted(out, key=lambda p: (-p["year"], p["bench"]))


def backfill_partition(client: httpx.Client, parser: Parser, part: dict, stats: Stats,
                       use_archives: bool) -> None:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, source, pdf_key FROM judgments WHERE text_extracted_at IS NULL "
            "AND source = %s AND starts_with(pdf_key, %s)", (part["source"], part["prefix"])).fetchall()
    if use_archives:
        pending = {PurePosixPath(r["pdf_key"]).name: r["id"] for r in rows}
        for archive in list_archives(client, part["year"], part["bench"], part["source"]):
            if not pending:
                break
            stream_archive(client, parser, archive, pending, stats)
        left = set(pending.values())
        rows = [r for r in rows if r["id"] in left]
        if rows:
            log.info("%d/%s: %d judgments not in the archives, fetching them one by one",
                     part["year"], part["bench"], len(rows))
    fetch_pdfs(client, parser, rows, stats, downloaders=DOWNLOADERS // 2 if use_archives else DOWNLOADERS)
    log.info("%d/%s: finished", part["year"], part["bench"])


def extract_text(limit: int | None = None, years: list[int] | None = None, workers: int | None = None,
                 streams: int = 4, via: str = "auto", retry_errors: bool = False,
                 client: httpx.Client | None = None) -> dict:
    """Fetch and parse PDFs for judgments that have no text yet.

    With `limit`, only the newest `limit` such judgments, one PDF at a time (what `update`
    needs). Without, every one, newest year first, streaming archives where it pays.
    """
    with db.connect() as conn:
        db.init_schema(conn)
        if retry_errors:
            conn.execute("UPDATE judgments SET text_extracted_at = NULL "
                         "WHERE text_error IS NOT NULL AND text_extracted_at IS NOT NULL")
            conn.commit()
        parts, latest = [], None
        if limit is None:
            parts = pending_partitions(conn, years)
        else:
            latest = conn.execute(
                "SELECT id, source, pdf_key FROM judgments WHERE text_extracted_at IS NULL "
                + ("AND substring(pdf_key from 'year=(\\d+)')::int = ANY(%(years)s) " if years else "")
                + "ORDER BY decision_date DESC NULLS LAST, id LIMIT %(limit)s",
                {"limit": limit, "years": years}).fetchall()

    stats = Stats()
    parser = Parser(workers)
    own_client = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(60, read=300),
                                    limits=httpx.Limits(max_connections=DOWNLOADERS * max(1, streams) + streams))
    try:
        if latest is not None:
            fetch_pdfs(client, parser, latest, stats)
        else:
            log.info("%d judgments without text in %d partitions",
                     sum(p["pending"] for p in parts), len(parts))

            def run(part):
                least = SC_ARCHIVE_MIN_PENDING if is_supreme(part["source"]) else ARCHIVE_MIN_PENDING
                use = via == "archive" or (via == "auto" and part["pending"] >= least)
                try:
                    backfill_partition(client, parser, part, stats, use)
                except Exception:
                    log.exception("%d/%s failed; a re-run picks it up", part["year"], part["bench"])

            with ThreadPoolExecutor(max(1, streams)) as ex:
                list(ex.map(run, parts))
    finally:
        parser.shutdown()
        if own_client:
            client.close()
    elapsed = time.monotonic() - stats.start
    return {"done": stats.done, "errors": stats.errors, "failed": stats.failed,
            "seconds": round(elapsed), "per_second": round(stats.done / max(elapsed, 1e-9), 1)}
