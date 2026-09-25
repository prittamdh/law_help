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

The act filter searches for the act's name as a phrase in the judgment text, so it only finds judgments whose text has been extracted (or whose opening lines name the act).

## API

| Endpoint | What it does |
| --- | --- |
| `GET /judgments` | Search and filter: `q` (full text), `judge`, `case_type`, `bench`, `disposal`, `decided_from`, `decided_to`, `page`, `page_size` |
| `GET /judgments/{id}` | One judgment with its description and extracted text |
| `GET /stats` | Totals by bench, the top judges, case types and outcomes |

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
