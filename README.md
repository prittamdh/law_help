# law_help

Case law in one place, organized and searchable: the **Supreme Court of India** and the **Rajasthan High Court** (Jaipur and Jodhpur benches).

## Where the data comes from

The Rajasthan HC website ([hcraj.nic.in](https://hcraj.nic.in/hcraj/index.php)) and eCourts need a captcha for every judgment search, which makes scraping them slow and fragile. So the first importer reads the public [Indian High Court Judgments](https://registry.opendata.aws/indian-high-court-judgments/) dataset on AWS instead. It is licensed CC-BY-4.0 and needs no AWS account. The dataset is built from eCourts, is refreshed regularly, and covers Rajasthan (court code `8_9`) from 1989 to date. 2024 alone has about 91,000 judgments and orders.

Each record gives us the case type, number and year, the parties, the judge or judges, the registration and decision dates, the outcome (allowed, dismissed and so on), the opening lines of the judgment, and a link to its PDF.

### Supreme Court

Supreme Court judgments come from the sister dataset, [Indian Supreme Court Judgments](https://registry.opendata.aws/indian-supreme-court-judgments/) (also CC-BY-4.0, no account), built from the Supreme Court's eSCR portal. It has every judgment reported in the Supreme Court Reports from 1950 to date, 43,547 in September 2026, and the English PDFs come to 22.7 GB (there are translations too, which we skip). Each record adds the neutral citation (`2024 INSC 735`), the S.C.R. citation (`[2024] 10 S.C.R. 108`), the full coram and the reporter's headnote, which is stored as the description and searched.

```bash
python -m law_help.importer supreme       # metadata for every year (~1 minute)
python -m law_help.importer text          # then the PDFs, like the High Court's
```

Rows have `court = 'Supreme Court of India'`, `bench = 'supreme court'` and `bench_strength` like `3-judge`. `law_help/supreme.py` reads the metadata and `extract.extract_sc` reads the report layout (parties either side of "v.", "[A and B, JJ.]", "Issue for Consideration", "Result of the Case"). `update` checks both datasets. The search has a court filter (`/judgments?court=supreme` or `court=rajasthan`).

## Quick start

```bash
docker compose up -d db               # Postgres 16 on localhost:5432 (user/pass: law/law)
pip install -e '.[dev]'

python -m law_help.importer metadata --year 2024            # both benches, one year (~30s)
python -m law_help.importer metadata                         # every year (1989 onward)
python -m law_help.importer text --year 2024                 # fetch PDFs, extract text and structure (~6 min)
python -m law_help.importer text                             # every judgment still without text
python -m law_help.importer structure                        # re-derive structure and "cited by" links from stored text

uvicorn law_help.api:app --reload     # search UI at http://localhost:8000, API docs at /docs
```

Set `DATABASE_URL` to point somewhere other than `postgresql://law:law@localhost:5432/law_help`.

The importer is idempotent. Re-running it updates existing rows by PDF link and keeps text that has already been extracted.

## Extracting the text

`importer text` fetches the PDF of every judgment that has no text yet, extracts the text with pdfium, and derives the structured fields below. It is safe to stop at any point and run again: judgments are committed a hundred at a time and a re-run carries on where the last one stopped.

For a backfill it streams the dataset's per-year tar archives (`data/tar/...`, 4 GB for Jaipur 2024) instead of making one request per PDF, and works on four year-bench partitions at once (`--streams`). Parsing runs on every CPU (`--workers`). `text_archives` remembers how far into each archive a run got, so an interrupted run resumes mid-archive with a Range request, and a dropped connection reconnects the same way. Partitions with fewer than 2,000 judgments left, and any judgment an archive lacks, are fetched one PDF at a time. `--limit N` does only the newest N one by one, which is what `update` uses.

On a 16-vCPU cloud sandbox, 2023 and 2024 (210,558 judgments, 14 GB of PDFs) took 12 minutes at 271 judgments a second, bound by CPU. The whole collection (about 1.1 million judgments, 79 GB) should take a little over an hour on the same machine, and the database grows by about 11 KB per judgment (12 GB in all).

A PDF that can't be parsed is marked done with `text_language = 'no-text'` and the reason in `text_error`. If the text is fine but deriving the structured fields fails, the text is kept and `text_error` starts with `structure:`. In both cases `--retry-errors` tries again. In 2023 and 2024 there were 10 unparseable files, all PDFs the dataset lists but doesn't have. A PDF that can't be downloaded is left for the next run.

## AI summaries

Every judgment with text gets these for free, from rules in `law_help/extract.py`: a one-line headline ("Bail application · NDPS Act s. 8, 21 · Bail granted"), a two-sentence extractive summary, the outcome, and for long judgments a "key passage" picked from the court's reasoning.

For a written summary (the issues decided and what the court held), `python -m law_help.summarize` asks a model and stores the result in `ai_summary`. By default it uses a model running locally in [Ollama](https://ollama.com), which is free. [docs/desktop-summaries.md](docs/desktop-summaries.md) has the steps for a Windows gaming PC.

```bash
ollama pull qwen3:14b
python -m law_help.summarize --min-chars 8000 --limit 100     # long judgments, newest first
```

Settings: `LAW_HELP_OLLAMA_MODEL` (default `qwen3:14b`) and `OLLAMA_HOST` (default `http://localhost:11434`). The Claude API is also supported but paid, so it stays off unless chosen with `LAW_HELP_SUMMARIZER=claude` and an `ANTHROPIC_API_KEY` (model: `LAW_HELP_SUMMARY_MODEL`, default `claude-opus-5`). Scans without text are skipped. Where no AI summary exists, the site shows the extractive one.

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
| `outcome` | what the court did, from its closing lines: `Bail granted`, `Withdrawn`, `Proceedings quashed`… |
| `key_reasoning` | long judgments only: three sentences that read most like the court's reasons |
| `text_language` | `en`, `hi` (Hindi: summary and outcome only), `hi-krutidev` (old-font Hindi not yet converted) or `no-text` (a scan) |

`acts_cited` and `cases_cited` have GIN indexes, so "every judgment citing Section 376 IPC" is
`acts_cited @> '[{"act": "Indian Penal Code, 1860", "sections": ["376"]}]'`.

About 4% of orders are Hindi typed in the old Kruti Dev font, whose text layer reads
"izkFkhZ dh vksj ls". `law_help/krutidev.py` converts these to Unicode Hindi ("प्रार्थी की ओर से")
while leaving the English case header alone, so they are readable and searchable in Hindi.
New judgments are converted as their text is extracted; for text extracted before, run
`python -m law_help.importer hindi` (no downloads; safe to run again). The text as extracted
is kept in `full_text_original`.

The rules are in `law_help/extract.py`. When they change, bump `EXTRACTOR_VERSION` and run
`structure` to re-derive every row from stored text without downloading anything again.
Accuracy on hand-labelled judgments is in [eval/README.md](eval/README.md).

### Cited by

Rajasthan HC orders cite the court's own earlier orders by case number, as in
"Kheta Ram Vs. State (S.B. Civil Writ Petition No.6863/2014) decided on 12.03.2015".
`structure` keeps those references in `case_refs` (with the party names beside them and the
seat, when the text names one), and then `importer citations` resolves them to judgments in
the `citations` table. `structure` and `update` run `citations` at the end, and it rebuilds
the whole table in a few seconds. A reference goes to the latest judgment in that case decided
before the citing one. Jaipur and Jodhpur reuse case numbers, so the case's title must contain
one of the cited party names, and a bare number held by both benches is left unlinked rather
than guessed. Neutral citations (`2024:RJ-JP:2823`) are linked too.

The judgment page lists "Cited by" (later judgments, with a common order that decided a batch
of connected cases shown once) and the earlier judgments of this court it cites. On 33,000
judgments from 2025, 15% of English orders cited at least one case of this court.

### Good law check

`python -m law_help.importer goodlaw` flags a judgment when a later judgment of this court
set it aside, recalled it or overruled it, and keeps the later judgment's own sentence as the
reason. It is rules only (`law_help/goodlaw.py`), and `structure` and `update` run it after
`citations`. It fills the `treatments` table:

- **Set aside on appeal**: a Division Bench special appeal (SAW) that says "the order dated
  05.04.2023 passed by the learned Single Judge is set aside". The order is found by that date
  at the same seat, and by its case number when the appeal names one. With no number, it must
  be a Single Judge's writ order whose parties match the appeal's by first name plus a second
  name or the father's name. When only a paragraph, a direction or costs are set aside, the
  flag reads "Partly set aside on appeal".
- **Recalled on review**: a review petition (WRW, CRW, CRLRW) that says the order "stands recalled".
- **Overruled**: a judgment that says an earlier judgment of this court, cited by case number
  or neutral citation, "is overruled" or "does not lay down the correct law".

A wrong flag would tell a lawyer to stop relying on good law, so the rules skip what counsel
argued or asked for ("submitted", "prayed"), anything negated or conditional ("no ground to set
aside", "if"), appeals the court dismissed, and any case where more than one order fits. On
2024 and 2025 appeals and reviews, every one of the 33 flags was checked by hand and was right.
The flag shows on the judgment page and as a red chip in search results. Orders the Supreme
Court set aside are not flagged yet.

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
| `GET /judgments/{id}` | One judgment with its description and extracted text, `cited_by`, `cites`, and `treated_by` (later judgments that set it aside, recalled or overruled it) |
| `GET /stats` | Totals by bench, the top judges, acts, case types and outcomes |

Every result carries a `pdf_url` that points at the original judgment PDF, and `good_law`:
`set_aside`, `partly_set_aside`, `recalled`, `overruled`, or null.

Until `text` has run, full-text search only sees the title and the opening lines of each judgment. So a search like `bail NDPS` finds few matches, because the statute is usually cited deeper in the judgment.

## Layout

- `law_help/schema.sql`: the `judgments` table, with a weighted `tsvector` (title > opening lines > full text)
- `law_help/parse.py`: splits eCourts titles into case type/number/year and parties, and parses bench composition and dates
- `law_help/importer.py`: the importer commands (metadata, supreme, text, structure, update)
- `law_help/supreme.py`: the Supreme Court dataset's layout and metadata
- `law_help/text.py`: parallel, resumable PDF download and text extraction
- `law_help/goodlaw.py`: the good law check (set aside, recalled, overruled)
- `law_help/extract.py`: pulls parties, advocates, judges, acts, cited cases and a summary out of judgment text
- `eval/`: hand-labelled judgments and the script that scores the extractor against them
- `law_help/summarize.py`: model-written summaries (summary, issues, holding, outcome) from local Ollama or the Claude API, stored in `ai_summary`
- `law_help/api.py`: the FastAPI search API
- `law_help/static/`: the search and judgment pages (plain HTML, CSS and JavaScript, no build step)

## Tests

```bash
docker compose exec db createdb -U law law_help_test   # once
pytest          # API tests use TEST_DATABASE_URL (default .../law_help_test) and skip if it is unreachable
```
