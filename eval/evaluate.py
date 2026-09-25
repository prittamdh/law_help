"""Score law_help.extract against 48 hand-checked Rajasthan HC judgments.

    python eval/evaluate.py            # downloads the PDFs once into .cache/
    python eval/evaluate.py --verbose  # also print every miss and false positive

gold.json was built by reading each PDF. Acts, sections and cited cases were labelled
from the judgment text; header fields (judges, first parties, advocate counts) were
checked against each PDF's first page. See eval/README.md for the results.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from law_help.extract import extract, pdf_text  # noqa: E402
from law_help.importer import BUCKET_URL, CACHE_DIR  # noqa: E402

HERE = Path(__file__).parent

OUTCOME_WORDS = {
    "ALLOWED": r"allowed|granted|quashed|released|enlarged|suspended|set aside",
    "DISMISSED": r"dismissed|rejected|not inclined|withdrawn",
    "WITHDRAWN": r"withdrawn|withdraw",
    "DISPOSED OF": r"disposed|withdrawn|quashed|direct",
}


def fetch(client: httpx.Client, key: str) -> bytes:
    cached = CACHE_DIR / key
    if not cached.exists():
        r = client.get(f"{BUCKET_URL}/{key}")
        r.raise_for_status()
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(r.content)
    return cached.read_bytes()


def norm_section(s: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", s.upper())


def norm_cite(s: str) -> str:
    return re.sub(r"[^0-9a-z]", "", s.lower().replace("online", "online"))


def prf(tp: int, fp: int, fn: int) -> str:
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    return f"precision {p:.0%} ({tp}/{tp + fp})  recall {r:.0%} ({tp}/{tp + fn})"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--set", choices=["tuning", "heldout"], default="heldout",
                    help="tuning: the 48 the rules were written against; heldout: 24 never tuned on")
    args = ap.parse_args()
    gold = json.loads((HERE / {"tuning": "gold.json", "heldout": "heldout.json"}[args.set]).read_text())
    c = Counter()
    notes: list[str] = []

    with httpx.Client(timeout=120) as client:
        for g in gold:
            e = extract(pdf_text(fetch(client, g["pdf_key"])) or "")
            name = g["pdf_key"].rsplit("/", 1)[-1]
            c["docs"] += 1
            c["lang_ok"] += e["language"] == g["language"]
            c["english"] += g["language"] == "en"

            # header
            for field, got in (("judges", e["judges"]), ("neutral_citation", e["neutral_citation"]),
                               ("petitioner", (e["petitioners"] or [None])[0]),
                               ("respondent", (e["respondents"] or [None])[0])):
                ok = got == g[field]
                c[f"{field}_ok"] += ok
                if not ok:
                    notes.append(f"{name} {field}: got {got!r}, want {g[field]!r}")
            got_adv = {k: len(v) for k, v in e["advocates"].items()}
            ok = got_adv == g["advocates"]
            c["advocates_ok"] += ok
            c["advocates_n"] += g["advocates"] != "unverified"
            if not ok and g["advocates"] != "unverified":
                notes.append(f"{name} advocates: got {e['advocates']}, want counts {g['advocates']}")
            meta = [j.strip() for j in (g["metadata_judges"] or "").split(",") if j.strip()]
            c["judges_match_metadata"] += meta == e["judges"]

            if g["language"] != "en" or "acts" not in g:
                continue
            c["labelled"] += 1

            # acts and sections
            got_acts = {a["act"]: {norm_section(s) for s in a["sections"]} for a in e["acts_cited"]}
            want_acts = {a: {norm_section(s) for s in ss} for a, ss in g["acts"].items()}
            for a in got_acts.keys() | want_acts.keys():
                if a in got_acts and a in want_acts:
                    c["act_tp"] += 1
                elif a in got_acts:
                    c["act_fp"] += 1
                    notes.append(f"{name} act false positive: {a}")
                else:
                    c["act_fn"] += 1
                    notes.append(f"{name} act missed: {a}")
                got_s, want_s = got_acts.get(a, set()), want_acts.get(a, set())
                c["sec_tp"] += len(got_s & want_s)
                c["sec_fp"] += len(got_s - want_s)
                c["sec_fn"] += len(want_s - got_s)
                for s in got_s - want_s:
                    notes.append(f"{name} section false positive: {a} {s}")
                for s in want_s - got_s:
                    notes.append(f"{name} section missed: {a} {s}")

            # cases cited
            named = [x for x in e["cases_cited"] if x["name"]]
            matched = set()
            for want in g["cases"]:
                hit = next((i for i, x in enumerate(named) if i not in matched and
                            all(k in x["name"].lower() for k in want["parties"])), None)
                if hit is None:
                    c["case_fn"] += 1
                    notes.append(f"{name} case missed: {' v. '.join(want['parties'])}")
                else:
                    matched.add(hit)
                    c["case_tp"] += 1
            for i, x in enumerate(named):
                if i not in matched:
                    c["case_fp"] += 1
                    notes.append(f"{name} case false positive: {x['name']}")
            got_c = {norm_cite(y) for x in e["cases_cited"] for y in x["citations"]}
            want_c = {norm_cite(y) for x in g["cases"] for y in x["citations"]}
            c["cite_tp"] += len(got_c & want_c)
            c["cite_fp"] += len(got_c - want_c)
            c["cite_fn"] += len(want_c - got_c)

            # summary: present, and its outcome agrees with the court's recorded disposal
            c["summary_present"] += bool(e["summary"])
            words = OUTCOME_WORDS.get(g["disposal_nature"] or "")
            if e["summary"] and words:
                c["summary_outcome_checked"] += 1
                ok = bool(re.search(words, e["summary"], re.I))
                c["summary_outcome_ok"] += ok
                if not ok:
                    notes.append(f"{name} summary outcome does not say {g['disposal_nature']}: {e['summary'][-160:]}")

    n, en = c["docs"], c["labelled"]
    print(f"{args.set}: {n} judgments, acts and citations labelled in {en} English ones\n")
    print(f"language detected correctly   {c['lang_ok']}/{n}")
    for f in ("neutral_citation", "judges", "petitioner", "respondent"):
        print(f"{f:<29} {c[f + '_ok']}/{n}")
    print(f"{'advocates (count per side)':<29} {c['advocates_ok']}/{c['advocates_n']}")
    print(f"judges agree with eCourts metadata {c['judges_match_metadata']}/{n}\n")
    print(f"acts cited (English)          {prf(c['act_tp'], c['act_fp'], c['act_fn'])}")
    print(f"sections cited (English)      {prf(c['sec_tp'], c['sec_fp'], c['sec_fn'])}")
    print(f"cases cited by name (English) {prf(c['case_tp'], c['case_fp'], c['case_fn'])}")
    print(f"reporter citations (English)  {prf(c['cite_tp'], c['cite_fp'], c['cite_fn'])}")
    print(f"summary present (English)     {c['summary_present']}/{en}")
    print(f"summary states the outcome    {c['summary_outcome_ok']}/{c['summary_outcome_checked']}")
    if args.verbose:
        print("\n" + "\n".join(notes))


if __name__ == "__main__":
    main()
