"""Model summaries, with Ollama and the Claude API replaced by fakes (no key, model or network needed).

The database tests use TEST_DATABASE_URL and skip when it is unreachable, like test_api.py.
"""

import json
import os
from types import SimpleNamespace

import anthropic
import httpx
import httpx2
import pytest
from fastapi.testclient import TestClient

from law_help import db, summarize
from law_help.importer import UPSERT_SQL
from law_help.summarize import JudgmentSummary, Unavailable

TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://law:law@localhost:5432/law_help_test")

RESULT = JudgmentSummary(
    summary="The accused sought bail in an NDPS case. The court granted bail because the "
            "recovered quantity was below the commercial limit.",
    issues=["Whether Section 37 of the NDPS Act bars bail for an intermediate quantity."],
    holding="Section 37 does not apply below the commercial quantity, so bail was granted.",
    outcome="Bail granted",
)


class FakeClient:
    """Stands in for anthropic.Anthropic: records each parse() call and replays `replies`."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if reply is None:
            return SimpleNamespace(stop_reason="refusal", parsed_output=None)
        return SimpleNamespace(stop_reason="end_turn", parsed_output=reply)


def _api_error():
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.InternalServerError("overloaded", response=httpx2.Response(500, request=request), body=None)


@pytest.fixture
def claude(monkeypatch):
    monkeypatch.setenv("LAW_HELP_SUMMARIZER", "claude")
    monkeypatch.delenv("LAW_HELP_SUMMARY_MODEL", raising=False)


@pytest.fixture(autouse=True)
def default_backend(monkeypatch):
    monkeypatch.delenv("LAW_HELP_SUMMARIZER", raising=False)
    monkeypatch.delenv("LAW_HELP_OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)


def test_summarize_sends_text_and_asks_for_structured_output(claude):
    fake = FakeClient(RESULT)
    assert summarize.summarize("ORDER ... bail granted.", "CRLMB/1/2024", client=fake) == RESULT
    call = fake.calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["output_format"] is JudgmentSummary
    assert call["fallbacks"] == "default"
    content = call["messages"][0]["content"]
    assert content.startswith("Case: CRLMB/1/2024") and "ORDER ... bail granted." in content


def test_model_can_be_overridden(claude, monkeypatch):
    monkeypatch.setenv("LAW_HELP_SUMMARY_MODEL", "claude-sonnet-5")
    fake = FakeClient(RESULT)
    summarize.summarize("text", client=fake)
    assert fake.calls[0]["model"] == "claude-sonnet-5"


def test_refusal_returns_none(claude):
    assert summarize.summarize("text", client=FakeClient(None)) is None


def test_claude_without_key_exits_with_a_hint(claude, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
        summarize.main([])


# --------------------------------------------------------------------------- Ollama


def fake_ollama(reply=RESULT, status=200, seen=None):
    """An httpx client whose transport plays Ollama's /api/chat."""
    def handler(request):
        if seen is not None:
            seen.append(json.loads(request.content))
        if status != 200:
            return httpx2_like(status)
        content = reply if isinstance(reply, str) else reply.model_dump_json()
        return httpx.Response(200, json={"message": {"role": "assistant", "content": content}, "done": True})
    return httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))


def httpx2_like(status):
    return httpx.Response(status, json={"error": "model not found"})


def test_ollama_is_the_default_and_sends_the_schema():
    seen = []
    assert summarize.backend() == "ollama" and summarize.model_label() == "ollama:qwen3:14b"
    assert summarize.summarize("ORDER ... bail granted.", "CRLMB/1/2024", client=fake_ollama(seen=seen)) == RESULT
    body = seen[0]
    assert body["model"] == "qwen3:14b" and body["stream"] is False and body["think"] is False
    assert body["format"]["properties"].keys() == {"summary", "issues", "holding", "outcome"}
    assert body["options"]["num_ctx"] == summarize.OLLAMA_CONTEXT
    assert "ORDER ... bail granted." in body["messages"][1]["content"]


def test_ollama_long_judgment_keeps_the_start_and_the_end():
    seen = []
    text = "START " + "x" * 100_000 + " END"
    summarize.summarize(text, client=fake_ollama(seen=seen))
    content = seen[0]["messages"][1]["content"]
    assert "START" in content and "END" in content and "omitted for length" in content
    assert len(content) < summarize.OLLAMA_MAX_CHARS + 500


def test_ollama_bad_json_is_a_failure_not_a_crash():
    assert summarize.summarize("text", client=fake_ollama(reply='{"summary": "cut off')) is None


def test_ollama_missing_model_stops_with_the_pull_command():
    with pytest.raises(Unavailable, match="ollama pull qwen3:14b"):
        summarize.summarize("text", client=fake_ollama(status=404))


def test_ollama_not_running_stops_with_a_hint():
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)
    client = httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(refuse))
    with pytest.raises(Unavailable, match="Ollama is not running"):
        summarize.summarize("text", client=client)


def _row(cnr, language, text):
    return {
        "source": "test", "court": "Rajasthan High Court", "bench": "jaipur", "cnr": cnr,
        "pdf_link": f"test/{cnr}.pdf", "pdf_key": f"data/pdf/test/{cnr}.pdf",
        "case_type": "CRLMB", "case_number": 1, "case_year": 2024, "title": f"CRLMB/1/2024 of {cnr}",
        "petitioner": "A", "respondent": "STATE", "judges": ["X"], "bench_strength": "single",
        "disposal_nature": "ALLOWED", "date_of_registration": "2024-01-01",
        "decision_date": "2024-02-01", "description": "Bail application", "_lang": language, "_text": text,
    }


@pytest.fixture
def conn(monkeypatch):
    try:
        conn = db.connect(TEST_URL)
    except Exception:
        pytest.skip("no test database available")
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    db.init_schema(conn)
    # Autocommit, so this connection's reads hold no locks while summarize_pending runs
    # init_schema (ALTER TABLE) on its own connection.
    conn.autocommit = True
    conn.execute("TRUNCATE judgments")
    rows = [_row("EN1", "en", "English order one"), _row("EN2", "en", "English order two"),
            _row("KD1", "hi-krutidev", "izkFkhZ gS dk"), _row("NT1", "no-text", None)]
    with conn.cursor() as cur:
        cur.executemany(UPSERT_SQL, rows)
        for r in rows:
            cur.execute("UPDATE judgments SET full_text = %s, text_language = %s, summary = 'Extractive.' "
                        "WHERE cnr = %s", (r["_text"], r["_lang"], r["cnr"]))
    yield conn
    conn.execute("TRUNCATE judgments")
    conn.close()


def test_summarize_pending_skips_krutidev_and_keeps_going_after_errors(conn, claude):
    fake = FakeClient(_api_error(), RESULT)
    assert summarize.summarize_pending(10, client=fake) == (1, 1)
    sent = {c["messages"][0]["content"].split("\n")[0] for c in fake.calls}
    assert sent == {"Case: CRLMB/1/2024 of EN1", "Case: CRLMB/1/2024 of EN2"}  # never KD1 or NT1

    rows = conn.execute("SELECT cnr, ai_summary, ai_summary_model, summary FROM judgments "
                        "WHERE ai_summary IS NOT NULL").fetchall()
    assert len(rows) == 1 and rows[0]["ai_summary"]["outcome"] == "Bail granted"
    assert rows[0]["ai_summary_model"] == "claude-opus-5" and rows[0]["summary"] == "Extractive."

    # A second run only picks up the one that failed.
    retry = FakeClient(RESULT)
    assert summarize.summarize_pending(10, client=retry) == (1, 0)
    assert len(retry.calls) == 1


def test_rate_limit_stops_the_batch(conn, claude):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    limited = anthropic.RateLimitError("slow down", response=httpx2.Response(429, request=request), body=None)
    with pytest.raises(Unavailable):
        summarize.summarize_pending(10, client=FakeClient(limited, RESULT))


def test_ollama_run_records_the_model_and_honours_min_chars(conn):
    seen = []
    assert summarize.summarize_pending(10, min_chars=len("English order one"), client=fake_ollama(seen=seen)) == (2, 0)
    assert summarize.summarize_pending(10, client=fake_ollama()) == (0, 0)  # nothing left
    models = {r["ai_summary_model"] for r in conn.execute("SELECT ai_summary_model FROM judgments "
                                                          "WHERE ai_summary IS NOT NULL")}
    assert models == {"ollama:qwen3:14b"}
    long_only = summarize.summarize_pending(10, redo=True, min_chars=10_000, client=fake_ollama())
    assert long_only == (0, 0)


def test_api_returns_ai_summary_next_to_the_extractive_one(conn):
    summarize.summarize_pending(1, client=fake_ollama())
    from law_help.api import app

    client = TestClient(app)
    results = client.get("/judgments", params={"q": "bail"}).json()["results"]
    with_ai = [r for r in results if r["ai_summary"]]
    assert len(with_ai) == 1 and with_ai[0]["summary"] == "Extractive."
    detail = client.get(f"/judgments/{with_ai[0]['id']}").json()
    assert detail["ai_summary"]["issues"] == RESULT.issues
