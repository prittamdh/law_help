# law_help

Case law in one place, organized and searchable. It starts with the **Rajasthan High Court** (Jaipur and Jodhpur benches).

## Where the data comes from

The Rajasthan HC website ([hcraj.nic.in](https://hcraj.nic.in/hcraj/index.php)) and eCourts need a captcha for every judgment search, which makes scraping them slow and fragile. So the first importer reads the public [Indian High Court Judgments](https://registry.opendata.aws/indian-high-court-judgments/) dataset on AWS instead. It is licensed CC-BY-4.0 and needs no AWS account. The dataset is built from eCourts, is refreshed regularly, and covers Rajasthan (court code `8_9`) from 1989 to date. 2024 alone has about 91,000 judgments and orders.

Each record gives us the case type, number and year, the parties, the judge or judges, the registration and decision dates, the outcome (allowed, dismissed and so on), the opening lines of the judgment, and a link to its PDF.

## Quick start

```bash
docker compose up -d db               # Postgres 16 on localhost:5432 (user/pass: law/law)
pip install -e '.[dev]'

python -m law_help.importer metadata --year 2024            # both benches, one year (~30s)
python -m law_help.importer metadata                         # every year (1989 onward)
python -m law_help.importer text --limit 500                 # fetch PDFs, extract text and structure
python -m law_help.importer structure                        # re-derive structure from stored text

uvicorn law_help.api:app --reload     # search UI at http://localhost:8000, API docs at /docs
```

Set `DATABASE_URL` to point somewhere other than `postgresql://law:law@localhost:5432/law_help`.

The importer is idempotent. Re-running it updates existing rows by PDF link and keeps text that has already been extracted.

## Structured fields

When `text` fetches a PDF it also derives these columns from the judgment itself:

| Column | Example |
| --- | --- |
| `neutral_citation` | `2024:RJ-JP:2823` |
| `parties` | `{"petitioners": ["Bhudev Singh"], "respondents": ["State Of Rajasthan, Through Principal Secretary, ..."]}` |
| `advocates` | `{"petitioner": ["Mr. Laxmikant Sharma"], "respondent": ["Mr. Syed Zakawat Ali, AGC"]}` |
| `bench_judges` | `{GANESH RAM MEENA}`, as printed on the judgment |
| `acts_cited` | `[{"act": "Indian Penal Code, 1860", "sections": ["354", "376"]}]` |
| `cases_cited` | `[{"name": "Gian Singh v. State of Punjab & Anr.", "citations": ["JT 2012(9) SC 426"]}]` |
| `summary` | the court's sentence on what was sought, then its sentence on the outcome |
| `text_language` | `en`, `hi-krutidev` (legacy Hindi font, body fields left empty) or `no-text` (a scan) |

`acts_cited` and `cases_cited` have GIN indexes, so "every judgment citing Section 376 IPC" is
`acts_cited @> '[{"act": "Indian Penal Code, 1860", "sections": ["376"]}]'`.

The rules are in `law_help/extract.py`. When they change, bump `EXTRACTOR_VERSION` and run
`structure` to re-derive every row from stored text without downloading anything again.
Accuracy on hand-labelled judgments is in [eval/README.md](eval/README.md).

## Search UI

`uvicorn` also serves a small search page at `/`. It has a keyword box plus filters for bench, decision dates, judge, act, case type and outcome, and each result opens a detail page with the case details, the extracted text and a link to the PDF. The search lives in the URL, so a search can be bookmarked or shared.

The act and section filters use the acts each judgment cites (see `importer structure`), so they only find judgments whose text has been extracted. The act box accepts short forms such as `IPC` or `NDPS Act`. The detail page also shows the summary, the neutral citation, advocates, the acts and sections cited (each one links to a search), and the cases cited.

## Keeping it current

`python -m law_help.importer update` is the one command to schedule daily (for example `0 6 * * *` in cron). It asks the bucket for the ETag of every year's metadata file (about 20 seconds), re-imports only the files that changed since the last run, then fetches PDFs and extracts text for up to `--text-limit` (default 1000) judgments that have none yet, newest first. ETags are stored in the `source_partitions` table. Two runs can't overlap, and a run that dies partway just redoes the unfinished file next time. On an empty database the first run imports every year. `--dry-run` lists what changed without importing.

The dataset is not refreshed daily. In 2026 its maintainers pushed Rajasthan updates on May 11, Jun 1, Jun 21, Jul 12, Jul 30, Aug 25, Sep 8 and Sep 15, so every one to four weeks, and each refresh covers judgments up to about five days earlier. The Jodhpur bench is thin for 2025 and 2026 (under 800 orders a year, against about 20,000 for Jaipur). The command prints a warning when the dataset hasn't changed for `--stale-days` (default 21). Getting same-day judgments would mean querying the HC's own judgment search, which asks for a captcha on every search, so this importer doesn't do that.

## API

| Endpoint | What it does |
| --- | --- |
| `GET /judgments` | Search and filter: `q` (full text), `judge` (matches the eCourts judges or the judges printed on the PDF), `act` and `section`, `case_type`, `bench`, `disposal`, `decided_from`, `decided_to`, `page`, `page_size` |
| `GET /judgments/{id}` | One judgment with its description and extracted text |
| `GET /stats` | Totals by bench, the top judges, acts, case types and outcomes |

Every result carries a `pdf_url` that points at the original judgment PDF.

Until `text` has run, full-text search only sees the title and the opening lines of each judgment. So a search like `bail NDPS` finds few matches, because the statute is usually cited deeper in the judgment.

## Layout

- `law_help/schema.sql`: the `judgments` table, with a weighted `tsvector` (title > opening lines > full text)
- `law_help/parse.py`: splits eCourts titles into case type/number/year and parties, and parses bench composition and dates
- `law_help/importer.py`: the metadata, PDF-text and structure importer
- `law_help/extract.py`: pulls parties, advocates, judges, acts, cited cases and a summary out of judgment text
- `eval/`: hand-labelled judgments and the script that scores the extractor against them
- `law_help/api.py`: the FastAPI search API
- `law_help/static/`: the search and judgment pages (plain HTML, CSS and JavaScript, no build step)

## Tests

```bash
docker compose exec db createdb -U law law_help_test   # once
pytest          # API tests use TEST_DATABASE_URL (default .../law_help_test) and skip if it is unreachable
```
