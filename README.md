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
python -m law_help.importer text --limit 500                 # fetch PDFs, extract full text

uvicorn law_help.api:app --reload     # http://localhost:8000/docs
```

Set `DATABASE_URL` to point somewhere other than `postgresql://law:law@localhost:5432/law_help`.

The importer is idempotent. Re-running it updates existing rows by PDF link and keeps text that has already been extracted.

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
- `law_help/importer.py`: the metadata and PDF-text importer
- `law_help/api.py`: the FastAPI search API

## Tests

```bash
docker compose exec db createdb -U law law_help_test   # once
pytest          # API tests use TEST_DATABASE_URL (default .../law_help_test) and skip if it is unreachable
```
