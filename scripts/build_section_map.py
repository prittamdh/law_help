"""Build law_help/data/section_map.json: which old section became which new one.

    python scripts/build_section_map.py SOURCE_DIR

IPC -> BNS comes from the "Corresponding Section Table of BNS with Repealed Act" in NCRB's
Sankalan edition of the BNS (ncrb.gov.in/uploads/SankalanPortal/DownloadPDF/BNS2023.pdf).
CrPC -> BNSS and Evidence Act -> BSA are matched by wording, since the new codes copy most
sections from the old ones: each new provision is paired with the old section it shares the
most text with (see match_by_text). Run build_bare_acts.py first.
"""

import json
import re
import sys
from collections import Counter
from math import log, sqrt
from pathlib import Path

import pypdfium2 as pdfium

DATA = Path(__file__).resolve().parent.parent / "law_help" / "data"

REF_RE = re.compile(r"^(\d{1,3}[A-Z]{0,3})((?:\s?\(\s?[0-9a-zA-Z]{1,5}\s?\))*)")


def _ref(s: str) -> str | None:
    """'376(3)', '65(2) ', '120A. Definition of ...' -> '376(3)', '65(2)', '120A'."""
    m = REF_RE.match(s.strip())
    return (m[1] + re.sub(r"\s", "", m[2])) if m else None


def _cells(page, split_x: float) -> tuple[list, list]:
    """The page's text lines as (top, bottom, text), in the left and right columns."""
    tp = page.get_textpage()
    rects = [tp.get_rect(i) for i in range(tp.count_rects())]
    cols = ([], [])
    for l, b, r, t in rects:
        text = tp.get_text_bounded(l, b, r, t).strip()
        if text:
            cols[l >= split_x].append([t, b, l, text])
    lines = []
    for col in cols:
        # Join pieces of one line (tops within 3 pt) left to right.
        col.sort(key=lambda x: -x[0])
        groups = []
        for piece in col:
            if groups and abs(groups[-1][0][0] - piece[0]) < 3:
                groups[-1].append(piece)
            else:
                groups.append([piece])
        merged = []
        for g in groups:
            g.sort(key=lambda x: x[2])
            text = re.sub(r"\s+", " ", " ".join(x[3] for x in g))
            merged.append([max(x[0] for x in g), min(x[1] for x in g), g[0][2], text])
        lines.append(merged)
    return lines[0], lines[1]


def _entries(lines: list, left_x: float) -> list[dict]:
    """Group lines into cells: a cell starts at a line flush left that opens with a number."""
    out = []
    for t, b, l, text in lines:
        starts = l < left_x + 4 and (_ref(text) or re.match(r"(?:Deleted|New Section|New Sub)", text))
        if starts or not out:
            out.append({"top": t, "bottom": b, "lines": [text]})
        else:
            out[-1]["bottom"] = b
            out[-1]["lines"].append(text)
    return out


def ncrb_table(pdf: Path) -> list[tuple[str, str]]:
    """[(new, old)] from NCRB's two-column table, e.g. ('65(2)', '376AB'), ('103(1)', '302')."""
    doc = pdfium.PdfDocument(pdf)
    pairs = []
    in_table = False
    for i in range(len(doc)):
        page = doc[i]
        text = page.get_textpage().get_text_range()
        if "CORRESPONDING SECTION TABLE" in text:
            in_table = True
        if not in_table:
            continue
        width = page.get_size()[0]
        left, right = _cells(page, width * 0.53)
        if not left or not right:
            continue
        lx = min(x[2] for x in left)
        rx = min(x[2] for x in right)
        lcells, rcells = _entries(left, lx), _entries(right, rx)
        for rc in rcells:
            olds = [r for r in (_ref(x) for x in rc["lines"]) if r]
            if not olds:
                continue
            # The new-code cell whose rows overlap this one most.
            best = max(lcells, default=None, key=lambda lc: min(lc["top"], rc["top"]) - max(lc["bottom"], rc["bottom"]))
            if best is None or min(best["top"], rc["top"]) - max(best["bottom"], rc["bottom"]) <= -2:
                continue
            # "351 (2), 351 (3)": one old section split over several new sub-sections.
            news = [r for r in (_ref(x) for x in best["lines"][0].split(",")) if r]
            if re.match(r"Deleted", best["lines"][0]):
                news = [None]
            pairs += [(new, old) for new in news for old in olds]
    return pairs


# --------------------------------------------------------------------------- matching by text

def _words(text: str) -> list[str]:
    text = re.sub(r"\b(?:Bharatiya Nyaya Sanhita|Bharatiya Nagarik Suraksha Sanhita|Bharatiya Sakshya "
                  r"Adhiniyam|Indian Penal Code|Code of Criminal Procedure|Indian Evidence Act)\b", " ", text)
    text = re.sub(r"\b(?:sanhita|adhiniyam|code|act)\b", " ", text.lower())
    return re.findall(r"[a-z]{3,}", text)


def _shingles(words: list[str], n: int = 3) -> Counter:
    return Counter(" ".join(words[i:i + n]) for i in range(max(1, len(words) - n + 1)))


def _parts(text: str) -> list[str]:
    """A section cut into its numbered sub-sections, or whole when it has none."""
    parts = re.split(r"(?:^|\n|\s)(?=\(\d{1,2}[A-Z]?\)\s|Provided that)", text)
    parts = [p.strip() for p in parts if len(p.strip()) > 40]
    return parts if len(parts) > 1 else [text]


def match_by_text(old_act: dict, new_act: dict, min_score: float = 0.35) -> list[dict]:
    """Pair each sub-section of each new section with the old section sharing the most wording.

    Returns [{"old": "438", "new": "482", "score": 0.93}], one row per (old, new section) pair.
    """
    old_secs = [s for s in old_act["sections"] if len(s["text"]) > 30]
    grams = [_shingles(_words(s["title"] + " " + s["text"])) for s in old_secs]
    df = Counter(g for c in grams for g in c)
    n = len(grams)
    idf = {g: log(n / d) for g, d in df.items()}
    norm = [sqrt(sum((v * idf[g]) ** 2 for g, v in c.items())) or 1 for c in grams]
    index: dict[str, list[int]] = {}
    for i, c in enumerate(grams):
        for g in c:
            index.setdefault(g, []).append(i)
    pairs: dict[tuple[str, str], float] = {}
    for sec in new_act["sections"]:
        for k, part in enumerate(_parts(sec["text"])):
            q = _shingles(_words((sec["title"] + " " if k == 0 else "") + part))
            scores: Counter = Counter()
            for g, v in q.items():
                for i in index.get(g, ()):
                    scores[i] += v * idf.get(g, 0) * grams[i][g] * idf.get(g, 0)
            if not scores:
                continue
            qn = sqrt(sum((v * idf.get(g, 0)) ** 2 for g, v in q.items())) or 1
            ranked = sorted(((s / (qn * norm[i]), i) for i, s in scores.items()), reverse=True)[:3]
            for rank, (cos, i) in enumerate(ranked):
                # Containment: the share of this provision's wording found in the old section.
                shared = sum(min(v, grams[i][g]) for g, v in q.items())
                size = sum(q.values())
                # Too short a provision is "contained" in many sections by chance.
                score = max(cos, shared / size) if size >= 15 else cos
                # The best match, and a runner-up only when it is nearly as close (a new section
                # that merges two old ones: BSA s. 23 is Evidence Act ss. 25, 26 and 27).
                if score >= min_score and (rank == 0 or score >= 0.85):
                    key = (old_secs[i]["number"], sec["number"])
                    pairs[key] = max(pairs.get(key, 0), round(score, 2))
    # Keep one new section per old one. When several match equally well (the trial chapters repeat
    # whole sentences), take the one whose place in the new code fits its neighbours' offsets.
    by_old: dict[str, list[tuple[float, str]]] = {}
    for (o, nw), sc in pairs.items():
        by_old.setdefault(o, []).append((sc, nw))
    order = [s["number"] for s in old_act["sections"] if s["number"] in by_old]
    num = lambda x: int(re.match(r"\d+", x)[0])
    sure = {o: max(c)[1] for o, c in by_old.items() if len([1 for sc, _ in c if sc >= max(c)[0] - 0.05]) == 1}
    out = []
    for k, o in enumerate(order):
        cands = by_old[o]
        best = max(sc for sc, _ in cands)
        tied = [nw for sc, nw in cands if sc >= best - 0.05]
        if len(tied) > 1:
            near = [num(sure[x]) - num(x) for x in order[max(0, k - 6):k + 7] if x in sure]
            offset = sorted(near)[len(near) // 2] if near else 0
            tied.sort(key=lambda nw: abs(num(nw) - num(o) - offset))
        out.append({"old": o, "new": tied[0], "score": pairs[(o, tied[0])]})
    return out


def _base(ref: str) -> str:
    return re.match(r"\d+[A-Z]*", ref)[0]


def _plausible(old_act: dict, old: str, new_act: dict, new: str) -> bool:
    index = lambda act: {s["number"]: s for s in act["sections"]}
    o, n = index(old_act).get(_base(old)), index(new_act).get(_base(new))
    if not o or not n:
        return True
    words = lambda s: set(re.findall(r"[a-z]{4,}", (s["title"] + " " + s["text"]).lower()))
    return len(words(o) & words(n)) >= 0.25 * max(1, len(words(o)))


def main(src_dir: str) -> None:
    acts = {p.stem: json.loads(p.read_text()) for p in (DATA / "acts").glob("*.json")}
    rows = []
    for new, old in ncrb_table(Path(src_dir) / "BNS2023.pdf"):
        if new and not _plausible(acts["ipc"], old, acts["bns"], new):
            print(f"dropped IPC {old} -> BNS {new}: the two share almost no wording (a misread table row)")
            continue
        rows.append({"old_act": "ipc", "old": old, "new_act": "bns", "new": new, "source": "ncrb"})
    for old_slug, new_slug in (("crpc", "bnss"), ("evidence", "bsa")):
        for p in match_by_text(acts[old_slug], acts[new_slug]):
            rows.append({"old_act": old_slug, "old": p["old"], "new_act": new_slug, "new": p["new"],
                         "source": "text", "score": p["score"]})
    seen, out = set(), []
    for r in rows:
        key = (r["old_act"], r["old"], r["new"])
        if key not in seen:
            seen.add(key)
            out.append(r)
    (DATA / "section_map.json").write_text(json.dumps(out, indent=0) + "\n")
    print(Counter(f"{r['old_act']}->{r['new_act']}" for r in out))


if __name__ == "__main__":
    main(sys.argv[1])
