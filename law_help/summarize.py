"""Model-written summaries of judgments, using the Claude API.

The extractive summary from law_help.extract copies two of the court's sentences, which is
thin for a long judgment. This asks Claude for a short plain-English summary, the questions
the court decided, what it held and the outcome, and stores it in `ai_summary`. The
extractive `summary` stays as the fallback wherever no model summary exists yet.

    export ANTHROPIC_API_KEY=...
    python -m law_help.summarize --limit 100     # newest judgments with text first

Only judgments with a readable text layer are sent: English, or Hindi in Unicode. Orders
typed in the legacy Kruti Dev font and scans with no text are skipped.
"""

import argparse
import logging
import os
import sys

from pydantic import BaseModel, Field

from . import db

DEFAULT_MODEL = "claude-opus-5"
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


def available() -> bool:
    """True when a Claude API credential is configured."""
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def model() -> str:
    return os.environ.get("LAW_HELP_SUMMARY_MODEL", DEFAULT_MODEL)


def _client():
    import anthropic

    return anthropic.Anthropic()


def summarize(text: str, title: str | None = None, client=None) -> JudgmentSummary | None:
    """Ask Claude for a structured summary of one judgment's text.

    Returns None when the model declines (a refusal from it and from its fallback).
    """
    client = client or _client()
    header = f"Case: {title}\n\n" if title else ""
    response = client.beta.messages.parse(
        model=model(),
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"{header}<judgment>\n{text}\n</judgment>"}],
        output_format=JudgmentSummary,
        output_config={"effort": "medium"},
        # A declined request is re-run server-side on Anthropic's recommended fallback model.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        return None
    return response.parsed_output


SELECT_SQL = """
SELECT id, title, full_text FROM judgments
WHERE full_text IS NOT NULL AND text_language = ANY(%(languages)s) {redo}
ORDER BY decision_date DESC NULLS LAST, id DESC
LIMIT %(limit)s
"""

UPDATE_SQL = """
UPDATE judgments SET ai_summary = %(ai_summary)s, ai_summary_model = %(model)s, ai_summarized_at = now()
WHERE id = %(id)s
"""


def summarize_pending(limit: int, redo: bool = False, client=None) -> tuple[int, int]:
    """Summarize judgments that have text but no model summary. Returns (done, failed)."""
    import anthropic
    from psycopg.types.json import Jsonb

    client = client or _client()
    done = failed = 0
    with db.connect() as conn:
        db.init_schema(conn)
        rows = conn.execute(
            SELECT_SQL.format(redo="" if redo else "AND ai_summary IS NULL"),
            {"languages": list(SUMMARIZABLE_LANGUAGES), "limit": limit},
        ).fetchall()
        for row in rows:
            try:
                result = summarize(row["full_text"], row["title"], client=client)
            except (anthropic.RateLimitError, anthropic.AuthenticationError,
                    anthropic.PermissionDeniedError):
                raise  # retrying the next row would fail the same way
            except anthropic.APIError as exc:  # one bad judgment should not stop the batch
                log.warning("could not summarize judgment %s: %s", row["id"], exc)
                failed += 1
                continue
            if result is None:
                log.warning("model declined to summarize judgment %s", row["id"])
                failed += 1
                continue
            conn.execute(UPDATE_SQL, {"id": row["id"], "model": model(),
                                      "ai_summary": Jsonb(result.model_dump())})
            conn.commit()
            done += 1
    return done, failed


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m law_help.summarize",
                                     description="write Claude summaries for judgments with text")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--all", action="store_true", help="redo judgments already summarized")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    if not available():
        sys.exit("Set ANTHROPIC_API_KEY to write model summaries. "
                 "Until then the site shows the extractive summary.")
    done, failed = summarize_pending(args.limit, args.all)
    print(f"summarized {done} judgments" + (f", {failed} failed" if failed else ""))


if __name__ == "__main__":
    main(sys.argv[1:])
