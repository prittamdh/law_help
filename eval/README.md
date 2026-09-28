# Extraction accuracy

`law_help/extract.py` pulls structured fields out of Rajasthan High Court judgment PDFs with
rules, no model. This folder measures how well it does against judgments labelled by hand.

```bash
python eval/evaluate.py --set heldout --verbose   # 24 judgments the rules were never tuned on
python eval/evaluate.py --set tuning              # 48 judgments the rules were written against
```

The script downloads each PDF from the AWS open dataset once (into `.cache/`) and prints
every miss and false positive with `--verbose`.

## The two sets

| Set | Judgments | Years | How chosen |
| --- | --- | --- | --- |
| `gold.json` (tuning) | 48 | 2023, 2024 | 12 at random from each bench and year |
| `heldout.json` | 24 | 2019, 2022 | per bench and year, the 2 longest of 20 random picks plus 4 more at random |

The held-out set leans towards long judgments on purpose, because random picks are mostly
one-page bail and adjournment orders with nothing to cite.

Acts, sections and cited cases were labelled from the judgment text before looking at what the
extractor produced. Header fields (judges, first petitioner and respondent, advocate counts)
were checked against each PDF's first page. Two held-out judgments are excluded from the act
and citation counts: one is 115,000 characters long, and one (a batch of customs appeals) was
first taken for a scan and not labelled.

## Results

The held-out numbers from the first run, before any rule was changed after seeing that set,
are the fairest estimate of accuracy on new judgments. The later column includes fixes made
after reading the held-out misses, so it flatters the extractor.

| Field | Tuning (48) | Held-out, first run | Held-out, after fixes |
| --- | --- | --- | --- |
| Language (English / legacy-font Hindi / no text) | 48/48 | 23/24 | 24/24 |
| Neutral citation, e.g. `2024:RJ-JP:2823` | 48/48 | 24/24 | 24/24 |
| Judges | 48/48 | 22/24 | 22/24 |
| First petitioner | 48/48 | 18/24 | 21/24 |
| First respondent | 48/48 | 18/24 | 22/24 |
| Advocates (count per side) | 47/48 | 19/22 | 20/22 |
| Acts cited: precision / recall | 100% / 97% | 90% / 86% | 91% / 91% |
| Sections cited: precision / recall | 100% / 97% | 97% / 84% | 97% / 84% |
| Cases cited by name: precision / recall | 100% / 100% | 100% / 89% | 100% / 100% |
| Reporter citations, e.g. `(2008) 12 SCC 661`: precision / recall | 100% / 100% | 100% / 87% | 100% / 93% |
| Summary names the outcome the court recorded | 37/41 | 19/21 | 19/21 |

Text now comes from pdfium instead of pypdf's layout mode (25 times faster). Every score above
is the same with pdfium. The customs appeal batch had been labelled `no-text` because pypdf
read almost nothing from it; pdfium reads its full text layer, so its label is now `en`.

Sections are scored per (act, section) pair, so `Section 376 IPC` found under the wrong act
counts as a miss and a false positive.

## What it gets wrong

- **Hindi orders in the Kruti Dev font (7 of 48, 15%).** Many bail orders are typed in a
  legacy Hindi font whose text layer is Latin gibberish (`izkFkhZx.k@vfHk;qDrx.k`). These are
  flagged `hi-krutidev`. Their English header still gives parties, advocates and judges, but
  acts, citations and the summary are left empty. A Kruti Dev to Unicode converter would
  unlock them.
- **Scanned PDFs.** Some older judgments are images with only a "Downloaded on" stamp as
  text. They are flagged `no-text` and need OCR.
- **"Section 149 of the Act".** A section that names its act only as "the Act" or "the Code"
  is dropped rather than guessed, which accounts for most missed sections in long judgments.
  Acts named without a year are only recognised when they are in the alias list.
- **Unusual headers.** OCR noise (`S.6.` for `S.B.`), an unnamed "Hon'ble the Chief Justice",
  and special appeals where the "petitioner" advocates are for the respondent side.
- **Summaries are extractive.** The summary is the court's own sentence on what was sought
  plus its sentence on the outcome, which reads well for short orders and thinly for long
  judgments. A model-written summary is the natural next step.
- **The eCourts metadata is sometimes wrong.** In one tuning judgment the metadata names a
  different judge from the one on the PDF, and in another it names no judge at all. The
  `bench_judges` column keeps what the PDF says.
