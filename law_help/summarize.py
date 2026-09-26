"""Model-written summaries of judgments, from a local Ollama model (free) or the Claude API.

The extractive summary from law_help.extract copies two of the court's sentences, which is
thin for a long judgment. This asks a model for a short plain-English summary, the questions
the court decided, what it held and the outcome, and stores it in `ai_summary`. The
extractive `summary` stays as the fallback wherever no model summary exists yet.

Backends, chosen with LAW_HELP_SUMMARIZER:

    ollama (default)  a model running on your own machine; costs nothing but electricity
        ollama pull qwen3:14b
        python -m law_help.summarize --min-chars 8000 --limit 1000   # long judgments first

    claude            the Claude API; paid, off unless chosen
        LAW_HELP_SUMMARIZER=claude ANTHROPIC_API_KEY=... python -m law_help.summarize

Only judgments with a readable text layer are sent: English, or Hindi in Unicode. Orders
typed in the legacy Kruti Dev font and scans with no text are skipped.
"""

import argparse
import json
import logging
import os
import sys
import time

import httpx
from pydantic import BaseModel, Field, ValidationError

from . import db

DEFAULT_CLAUDE_MODEL = "claude-opus-5"
# Qwen3 14B at 4-bit is ~9 GB, which leaves room on a 16 GB card for a 16k-token context.
DEFAULT_OLLAMA_MODEL = "qwen3:14b"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"
OLLAMA_CONTEXT = 16384
# What fits in OLLAMA_CONTEXT with the prompt and the answer, at ~3 characters a token.
OLLAMA_MAX_CHARS = 40000
SUMMARIZABLE_LANGUAGES = ("en", "hi")

log = logging.getLogger("law_help.summarize")


class JudgmentSummary(BaseModel):
    summary: str = Field(description="3 to 5 plain-English sentences: who came to court, "
                                     "for what, and what the court decided and why")
    issues: list[str] = Field(description="the questions of law or fact the court decided, "
                                          "one short sentence each")
    holding: str = Field(description="what the court held and the main reasons, in 1 to 3 sentences")
    outcome: str = Field(description="a few words, e.g. 'Bail granted' or 'Writ petition dismissed'")


SYSTEM_PROMPT = """\
You summarize judgments and orders of the Rajasthan High Court for practising lawyers.

Write in plain English, even when the judgment is in Hindi. Be accurate and specific: name \
the statutes and sections the court relied on, and the precedents only when the decision \
turns on them. State only what the judgment says; do not add outside facts or opinions. \
A short procedural order (an adjournment, a withdrawal, a disposal in terms of another \
case) gets a one or two sentence summary, an empty issues list, and a holding that says \
what was ordered."""


class Unavailable(Exception):
    """The backend can't be used at all (not running, no key): stop the batch."""


def backend() -> str:
    name = os.environ.get("LAW_HELP_SUMMARIZER", "ollama").lower()
    if name not in ("ollama", "claude"):
        raise Unavailable(f"LAW_HELP_SUMMARIZER must be 'ollama' or 'claude', not {name!r}")
    return name


def model() -> str:
    if backend() == "claude":
        return os.environ.get("LAW_HELP_SUMMARY_MODEL", DEFAULT_CLAUDE_MODEL)
    return os.environ.get("LAW_HELP_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)


def model_label() -> str:
    """What `ai_summary_model` records, e.g. 'ollama:qwen3:14b' or 'claude-opus-5'."""
    return f"ollama:{model()}" if backend() == "ollama" else model()


def _user_message(text: str, title: str | None, max_chars: int | None = None) -> str:
    if max_chars and len(text) > max_chars:
        # Keep the opening (parties, prayer, facts) and the end (reasons, order).
        half = max_chars // 2
        text = f"{text[:half]}\n\n[... middle of the judgment omitted for length ...]\n\n{text[-half:]}"
    header = f"Case: {title}\n\n" if title else ""
    return f"{header}<judgment>\n{text}\n</judgment>"


# --------------------------------------------------------------------------- Ollama


def ollama_host() -> str:
    return os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST).rstrip("/")


def _ollama_client() -> httpx.Client:
    host = ollama_host()
    if not host.startswith("http"):
        host = f"http://{host}"
    # A long judgment can take a minute or two on a consumer GPU, longer on a CPU.
    return httpx.Client(base_url=host, timeout=httpx.Timeout(600, connect=5))


def _summarize_ollama(text: str, title: str | None, client: httpx.Client) -> JudgmentSummary | None:
    try:
        r = client.post("/api/chat", json={
            "model": model(),
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _user_message(text, title, OLLAMA_MAX_CHARS)},
            ],
            "format": JudgmentSummary.model_json_schema(),  # Ollama constrains output to it
            "think": False,  # Qwen3's thinking mode triples the time for little gain here
            "stream": False,
            "options": {"num_ctx": OLLAMA_CONTEXT, "temperature": 0},
        })
    except httpx.ConnectError as exc:
        raise Unavailable(f"Ollama is not running at {ollama_host()} ({exc}). Start Ollama, or set "
                          "OLLAMA_HOST.") from exc
    if r.status_code == 404:
        raise Unavailable(f"Ollama has no model {model()!r}. Run: ollama pull {model()}")
    r.raise_for_status()
    content = r.json()["message"]["content"]
    try:
        return JudgmentSummary.model_validate_json(content)
    except ValidationError:
        log.warning("model returned something that is not a summary: %.200s", content)
        return None


# --------------------------------------------------------------------------- Claude


def _claude_client():
    import anthropic

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise Unavailable("Set ANTHROPIC_API_KEY to use the Claude backend.")
    return anthropic.Anthropic()


def _summarize_claude(text: str, title: str | None, client) -> JudgmentSummary | None:
    import anthropic

    try:
        response = client.beta.messages.parse(
            model=model(),
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _user_message(text, title)}],
            output_format=JudgmentSummary,
            output_config={"effort": "medium"},
            # A declined request is re-run server-side on Anthropic's recommended fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.RateLimitError) as exc:
        raise Unavailable(str(exc)) from exc
    if response.stop_reason == "refusal":
        return None
    return response.parsed_output


# --------------------------------------------------------------------------- either


def make_client():
    return _ollama_client() if backend() == "ollama" else _claude_client()


def summarize(text: str, title: str | None = None, client=None) -> JudgmentSummary | None:
    """A structured summary of one judgment's text, or None when the model gives no usable one.

    Raises Unavailable when the backend can't be used at all, and the backend's own error
    (httpx.HTTPError, anthropic.APIError) for a failure that may not recur on the next judgment.
    """
    client = client or make_client()
    if backend() == "ollama":
        return _summarize_ollama(text, title, client)
    return _summarize_claude(text, title, client)


SELECT_SQL = """
SELECT id, title, full_text FROM judgments
WHERE full_text IS NOT NULL AND text_language = ANY(%(languages)s)
  AND length(full_text) >= %(min_chars)s {redo}
ORDER BY decision_date DESC NULLS LAST, id DESC
LIMIT %(limit)s
"""

UPDATE_SQL = """
UPDATE judgments SET ai_summary = %(ai_summary)s, ai_summary_model = %(model)s, ai_summarized_at = now()
WHERE id = %(id)s
"""


def _transient_errors() -> tuple[type[Exception], ...]:
    errors: tuple[type[Exception], ...] = (httpx.HTTPError, json.JSONDecodeError, KeyError)
    try:
        import anthropic

        errors += (anthropic.APIError,)
    except ImportError:
        pass
    return errors


def summarize_pending(limit: int, redo: bool = False, min_chars: int = 0, client=None) -> tuple[int, int]:
    """Summarize judgments that have text but no model summary, newest first. Returns (done, failed)."""
    from psycopg.types.json import Jsonb

    client = client or make_client()
    transient = _transient_errors()
    done = failed = 0
    with db.connect() as conn:
        db.init_schema(conn)
        rows = conn.execute(
            SELECT_SQL.format(redo="" if redo else "AND ai_summary IS NULL"),
            {"languages": list(SUMMARIZABLE_LANGUAGES), "limit": limit, "min_chars": min_chars},
        ).fetchall()
        conn.commit()
        for row in rows:
            started = time.monotonic()
            try:
                result = summarize(row["full_text"], row["title"], client=client)
            except transient as exc:  # one bad judgment should not stop the batch
                log.warning("could not summarize judgment %s: %s", row["id"], exc)
                failed += 1
                continue
            if result is None:
                log.warning("no usable summary for judgment %s", row["id"])
                failed += 1
                continue
            conn.execute(UPDATE_SQL, {"id": row["id"], "model": model_label(),
                                      "ai_summary": Jsonb(result.model_dump())})
            conn.commit()
            done += 1
            log.info("summarized judgment %s (%d chars) in %.1fs", row["id"], len(row["full_text"]),
                     time.monotonic() - started)
    return done, failed


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m law_help.summarize",
                                     description="write model summaries for judgments with text")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--min-chars", type=int, default=0,
                        help="only judgments at least this long, e.g. 8000 for the long ones")
    parser.add_argument("--all", action="store_true", help="redo judgments already summarized")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    try:
        log.info("summarizing with %s", model_label())
        done, failed = summarize_pending(args.limit, args.all, args.min_chars)
    except Unavailable as exc:
        sys.exit(str(exc))
    print(f"summarized {done} judgments" + (f", {failed} failed" if failed else ""))


if __name__ == "__main__":
    main(sys.argv[1:])
