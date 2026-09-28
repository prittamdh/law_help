"""Build law_help/data/acts/*.json, the bare acts the site shows, from published texts.

Not needed to run the site: the JSON it writes is committed. Run it only to refresh an act:

    python scripts/build_bare_acts.py SOURCE_DIR

SOURCE_DIR holds the files named in ACTS: India Code PDFs (indiacode.nic.in), the official
gazette BNSS, and for three older acts the JSON of github.com/civictech-India/Indian-Law-Penal-Code-Json.
An India Code PDF is the "Download" link on the act's page (search the act's name at
https://www.indiacode.nic.in); save it under the file name ACTS gives. Acts whose file is
missing are skipped, so a SOURCE_DIR holding only the new PDFs rebuilds only those acts.
The text is Indian legislation, which s. 52(1)(q) of the Copyright Act, 1957 lets anyone reproduce.
"""

import json
import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as raw

OUT_DIR = Path(__file__).resolve().parent.parent / "law_help" / "data" / "acts"
CIVICTECH = "civictech-India/Indian-Law-Penal-Code-Json"
# Headings no source prints legibly.
TITLES = {("bnss", "301"): "Definitions"}

# slug, canonical name (as law_help.extract.canonical_act spells it), short name, kind, source file,
# where the file came from, and a second source for sections the first one lacks.
ACTS = [
    ("ipc", "Indian Penal Code, 1860", "IPC", "section", "THE INDIAN PENAL CODE.pdf",
     "India Code", "ipc.json"),
    ("bns", "Bharatiya Nyaya Sanhita, 2023", "BNS", "section", "The Bharatiya Nyaya Sanhita, 2023.pdf",
     "India Code, as on 6 October 2025", None),
    ("crpc", "Code of Criminal Procedure, 1973", "CrPC", "section",
     "THE CODE OF CRIMINAL PROCEDURE, 1973.pdf", "India Code", None),
    ("bnss", "Bharatiya Nagarik Suraksha Sanhita, 2023", "BNSS", "section",
     "The Bharatiya Nagarik Suraksha Sanhita, 2023.pdf", "India Code",
     "250884_2_english_01042024.pdf"),
    ("evidence", "Indian Evidence Act, 1872", "Evidence Act", "section", "iea.json", CIVICTECH, None),
    ("bsa", "Bharatiya Sakshya Adhiniyam, 2023", "BSA", "section",
     "he Bharatiya Sakshya Adhiniyam, 2023.pdf", "India Code, as on 6 October 2025", None),
    ("cpc", "Code of Civil Procedure, 1908", "CPC", "section", "THE CODE OF CIVIL PROCEDURE, 1908.pdf",
     "India Code, as on 10 January 2026", None),
    ("constitution", "Constitution of India", "Constitution", "article", "The Constitution of India.pdf",
     "Legislative Department, as on 1 May 2024", None),
    ("ni-act", "Negotiable Instruments Act, 1881", "NI Act", "section", "nia.json", CIVICTECH, None),
    ("mv-act", "Motor Vehicles Act, 1988", "MV Act", "section", "MVA.json", CIVICTECH, None),
    ("contract", "Indian Contract Act, 1872", "Contract Act", "section",
     "THE INDIAN CONTRACT ACT, 1872.pdf", "India Code", None),
    ("it-act", "Information Technology Act, 2000", "IT Act", "section",
     "THE INFORMATION TECHNOLOGY ACT, 2000.pdf", "India Code", None),
    ("ndps", "Narcotic Drugs and Psychotropic Substances Act, 1985", "NDPS Act", "section",
     "THE NARCOTIC DRUGS AND PSYCHOTROPIC SUBSTANCES ACT, 1985.pdf", "India Code", None),
    ("pocso", "Protection of Children from Sexual Offences Act, 2012", "POCSO Act", "section",
     "THE PROTECTION OF CHILDREN FROM SEXUAL OFFENCES ACT, 2012.pdf", "India Code", None),
    ("sc-st-act", "Scheduled Castes and Scheduled Tribes (Prevention of Atrocities) Act, 1989", "SC/ST Act",
     "section", "THE SCHEDULED CASTES AND THE SCHEDULED TRIBES (PREVENTION OF ATROCITIES) ACT, 1989.pdf",
     "India Code", None),
    ("arms-act", "Arms Act, 1959", "Arms Act", "section", "THE ARMS ACT, 1959.pdf", "India Code", None),
]

BODY_PT = 8.0     # superscript footnote markers are 6-7 pt; text is 9-11 pt
NOISE = re.compile(r"^\s*(?:\d{1,3}|Copyright ©.*|Page \d+ of \d+)\s*$")


def page_lines(page) -> list[str]:
    """The page's text without superscript markers or the footnotes under the rule."""
    tp = page.get_textpage()
    chars = []
    for i in range(tp.count_chars()):
        ch = chr(raw.FPDFText_GetUnicode(tp, i))
        size = raw.FPDFText_GetFontSize(tp, i)
        if ch in "\r\n" or size >= BODY_PT or (size <= 1.5 and ch.isspace()):
            chars.append(ch)
        elif size < BODY_PT and ch.strip():
            # A marker glued to the next word ("in 2[India]") or standing alone on a line.
            continue
        else:
            chars.append(ch)
    lines = "".join(chars).replace("\r", "").replace("￾", "-").replace("­", "").split("\n")
    lines = [ln.rstrip() for ln in lines]
    # Footnotes: after the last blank line, a run of lines opening "1. Subs. by ..." or "* ...".
    for k in range(len(lines) - 1, -1, -1):
        if not lines[k].strip() and k + 1 < len(lines) and re.match(
                r"^\s*(?:\d{1,3}\.\s+(?:Subs|Ins|Added|Omitted|The|Rep|Cl|Sub|Now|See|For|Vide|Amended|"
                r"Came|Section|Clause|Renumbered|Rule|Explanation|Proviso|Item|Words|Brought|Enforced|"
                r"Extended|This|In|S\.|w\.e\.f|Declared|Re-|Ref)|\*)", lines[k + 1]):
            lines = lines[:k]
            break
    while lines and (not lines[0].strip() or NOISE.match(lines[0])):
        lines.pop(0)
    return [ln for ln in lines if not re.match(r"^\s*Copyright ©", ln)]


# A footnote line that the page layout put somewhere other than the bottom of the page.
FOOTNOTE_RE = re.compile(
    r"^\s*\d{1,3}\.\s+(?!\[)(?:(?:Ins|Subs|Rep|Omitted|Added|Inserted|Substituted|Renumbered)\b"
    r"|.*(?:\bAct \d+ of \d{4}|\bA\. ?O\. \d{4}|w\.e\.f\.|\bibid\b|\bOrd\. \d+ of \d{4}))")


def pdf_text(path: Path, sized: bool = True) -> str:
    """sized=False for a PDF whose text is scaled, so font sizes can't tell markers from text."""
    doc = pdfium.PdfDocument(path)
    if not sized:
        return "\n".join(doc[i].get_textpage().get_text_range().replace("\r", "") for i in range(len(doc)))
    lines = [ln for i in range(len(doc)) for ln in page_lines(doc[i])]
    return "\n".join(ln for ln in lines if not FOOTNOTE_RE.match(ln))


def _num_key(n: str) -> tuple:
    m = re.match(r"(\d+)([A-Z]*)", n)
    return (int(m[1]), m[2]) if m else (0, n)


# "302. Punishment for murder.—Whoever ...", "[4. Extension of Code ...—", "(44) Search of place ...—"
HEAD_RE = re.compile(
    r"^[ \t]*\*?\[?[ \t]*(?:(?P<num>\d{1,3}(?:-?[A-Z]{1,3})?)\.[ \t]*(?:[—–][ \t]*)?"
    r"|\((?P<pnum>\d{1,3}[A-Z]{0,2})\)[ \t]*)"
    r"(?P<title>[A-Z\u201c\u2018\"'\[][^\n]*?(?:\n(?![ \t]*(?:\(|\[?\d{1,3}[A-Z]{0,3}\.\s))[^\n]{2,}?){0,2}?)"
    r"(?:\.|:)?[ \t]*(?:[—–]{1,2}|-{2}|\.\s*\][ \t]*|\][ \t]*(?=Omitted|Rep))",
    re.M,
)
CHAPTER_RE = re.compile(
    r"^\s*(?:\[\s*)?(CHAPTER|PART|ORDER)\s+([IVXLC]+[A-Z]*)\s*$(?:\n(?!CHAPTER|PART)(?P<name>[A-Z][A-Z ,'&()-]{3,}$))?", re.M)


def _clean(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    # Keep line breaks only where a new provision, clause or proviso starts.
    text = re.sub(r"\n(?!\s*(?:\(|Explanation|Illustration|Provided|Exception|[a-z]\)|\[?\d+\.|"
                  r"First|Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth|Ninth|Tenth|\*))", " ", text)
    return re.sub(r" {2,}", " ", text).strip()


def _unglue(name: str, vocab: Counter) -> str:
    """'Of offencesaffectingthe human body' -> 'Of offences affecting the human body' (the PDF drops
    spaces in some headings): a long word that splits into words the act uses more often is split."""
    def split(word: str, whole: bool) -> list[str] | None:
        if whole and vocab[word.lower()] >= 1:
            return [word]
        for i in range(len(word) - 1, 1, -1):
            if vocab[word[:i].lower()] >= 1 and (rest := split(word[i:], True)):
                return [word[:i], *rest]
        return None
    out = []
    for w in name.split():
        parts = split(w, False) if len(w) > 9 else None
        out += parts if parts and vocab[w.lower()] <= min(vocab[p.lower()] for p in parts) else [w]
    return " ".join(out)


def _number(m) -> str:
    return (m["num"] or m["pnum"]).replace("-", "")


TOC_RE = re.compile(r"^[ \t]*\[?(\d{1,3}[A-Z]{0,3})\.[ \t]*([A-Z\u201c\"\[][^\n]{2,})$", re.M)


def _toc(text: str) -> dict[str, str]:
    """The arrangement of sections before the body: {"302": "Punishment for murder", ...}."""
    out = {}
    text = re.sub(r"(?<=\.) (?=\d{1,3}[A-Z]{0,3}\. [A-Z])", "\n", text)
    for m in TOC_RE.finditer(text):
        out.setdefault(m[1], m[2].strip(" .[]"))
    return out


def split_sections(text: str, parens: bool = False) -> list[dict]:
    """[{"number", "title", "chapter", "text"}] in order, from the body after the table of contents.

    parens: the PDF prints some section numbers as "(44)" (one BNSS edition does).
    """
    # The body starts at "1. <title>—"; the table of contents has no dashes.
    starts = [m for m in HEAD_RE.finditer(text) if m["num"] == "1" and re.search("[—–]|--", m[0])]
    toc = _toc(text[:starts[0].start()]) if starts else {}
    # Start at the chapter heading just before section 1, so section 1 gets its chapter.
    body_at = starts[0].start() if starts else 0
    pre = [m for m in CHAPTER_RE.finditer(text[max(0, body_at - 300):body_at]) if m[1] != "ORDER"]
    body = text[body_at - (300 if body_at >= 300 else body_at) + pre[-1].start():] if pre else text[body_at:]
    # Headings run in order, so keep the longest increasing run of candidates: a number out of
    # order is a list item or a quoted provision.
    cands = [m for m in HEAD_RE.finditer(body) if m["num"] or parens]
    keys = [_num_key(_number(m)) for m in cands]
    best, prev = [1] * len(cands), [-1] * len(cands)
    for i in range(len(cands)):
        for j in range(max(0, i - 60), i):
            if keys[j] < keys[i] and keys[i][0] - keys[j][0] <= 30 and best[j] + 1 > best[i]:
                best[i], prev[i] = best[j] + 1, j
    chain, i = [], max(range(len(cands)), key=best.__getitem__) if cands else -1
    while i >= 0:
        chain.append(cands[i])
        i = prev[i]
    # (start, end of heading, number, title)
    heads = [(m.start(), m.end("title"), _number(m), m["title"]) for m in reversed(chain)]
    if toc:
        # Past the last section in the table of contents is a schedule.
        last = max(toc, key=_num_key)
        heads = [h for h in heads if _num_key(h[2]) <= _num_key(last)]
        # A heading the layout glued to the end of the previous paragraph: "...Act, 2023.] 335. Claims".
        found = {h[2] for h in heads}
        for num, title in toc.items():
            if num in found or title.startswith(("Omitted", "Repealed")):
                continue
            words = re.escape(title[:30]).replace(r"\ ", r"\s+")
            m = re.search(rf"(?<![\w(])\[?{num}\.?\s*\[?{words}[^\n]*?(?:\n[^\n]*?){{0,2}}?[.:]?\s*[—–]", body)
            if m:
                heads.append((m.start(), m.end(), num, title))
        heads.sort()
    chapters = [(m.start(), f"{m[1].title()} {m[2]}" + (f": {m['name'].strip().capitalize()}" if m["name"] else ""))
                for m in CHAPTER_RE.finditer(body)]
    vocab = Counter(w for w in re.findall(r"[a-z]+", body.lower()) if len(w) > 1 or w == "a")
    chapters = [(pos, _unglue(name, vocab)) for pos, name in chapters]
    out = []
    for i, (start, head_end, num, title) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(body)
        chunk = body[head_end:end]
        chunk = re.sub(r"^\s*(?:\.|:)?\s*(?:[—–]{1,2}|-{2})", "", chunk)
        # Drop a trailing chapter heading that belongs to the next section.
        nxt = list(CHAPTER_RE.finditer(chunk))
        if nxt and nxt[-1].start() > len(chunk) * 0.5:
            chunk = chunk[:nxt[-1].start()]
        # And a sub-heading that belongs to the next group of sections: "Of attempt".
        chunk = re.sub(r"(?<=[.;:\]])[ \t]*\n[ \t]*(?:Of|OF) [^\n.]{2,90}\s*$", "", chunk)
        chapter = next((name for pos, name in reversed(chapters) if pos < start), None)
        title = re.sub(r"\s+", " ", title).strip(" .[]")
        out.append({"number": num, "title": title, "chapter": chapter, "text": _clean(chunk)})
    return out


ORDER_RE = re.compile(r"^\[?ORDER ([IVXL]+(?:-?[A-Z])?)\]?[ \t]*\n([^\n]*)", re.M)


def split_cpc(text: str) -> list[dict]:
    """The Code's sections, then each Order's rules numbered "Order VII Rule 11"."""
    body_at = [m.start() for m in re.finditer(r"^THE FIRST SCHEDULE", text, re.M)][-1]
    sections = split_sections(text[:body_at])
    schedule = text[body_at:]
    orders = list(ORDER_RE.finditer(schedule))
    for i, m in enumerate(orders):
        block = schedule[m.start():orders[i + 1].start() if i + 1 < len(orders) else len(schedule)]
        order = m[1].replace("-", "")
        name = m[2].strip(" .[]")
        name = name.capitalize() if name.isupper() else name
        for rule in split_sections(block):
            rule.update(number=f"Order {order} Rule {rule['number']}", chapter=f"Order {order}: {name}")
            sections.append(rule)
    return sections


def load_civictech(path: Path) -> list[dict]:
    out = []
    for row in json.loads(path.read_text()):
        num = str(row.get("section", row.get("Section")))
        text = row.get("section_desc", row.get("description", ""))
        text = re.sub(r"\n{2,}", "\n", re.sub(r"[ \t]+", " ", text)).strip()
        chapter = row.get("chapter_title") or row.get("chapter")
        out.append({"number": num, "title": (row.get("section_title") or row.get("title") or "").strip(" .[]"),
                    "chapter": f"Chapter {row['chapter']}" if isinstance(chapter, int) else
                    (f"Chapter {row['chapter']}: {chapter.capitalize()}" if chapter else None),
                    "text": text})
    return out


def _key_text(text: str) -> str:
    return re.sub(r"\W", "", text[:150].lower())


def gazette_sections(text: str, wanted: set[str]) -> dict[str, str]:
    """Section text from the gazette, whose headings are margin notes: "301. In this Chapter,—"."""
    text = re.sub(r"(?m)^.*(?:THE GAZETTE OF INDIA|_{5,}|^SEC\. \d\]).*\n", "", text)
    out = {}
    for num in wanted:
        nxt = str(int(re.match(r"\d+", num)[0]) + 1)
        m = re.search(rf"^{num}\. (.*?)(?:^{nxt}\. |^THE (?:FIRST )?SCHEDULE)", text, re.M | re.S)
        if m:
            out[num] = _clean(m[1])
    return out


def _drop_markers(text: str) -> str:
    """Footnote numbers the Constitution prints at text size: "The President 4[may", "notification6 ,5"."""
    text = re.sub(r"(?<=[\s(])\d{1,2}\s?(?=\[)", "", text)
    text = re.sub(r"(?<=[a-z,;.)\]])\s?\d{1,2}(?=\s*(?:[,;.]|\*|\n|$))", "", text)
    text = re.sub(r"\s?\d{1,2}\s*\n?(?=\*\*\*)", " ", text)
    return re.sub(r"[ \t]{2,}", " ", text)


def build(slug, name, short, kind, source, origin, extra, src_dir: Path) -> dict:
    path = src_dir / source
    if path.suffix == ".json":
        sections = load_civictech(path)
    else:
        text = pdf_text(path)
        if slug == "cpc":
            sections = split_cpc(text)
        else:
            sections = split_sections(text, parens=slug == "bnss")
            if slug == "constitution":
                for sec in sections:
                    sec["text"] = _drop_markers(sec["text"])
            # A heading the PDF garbled beyond parsing: take the text from the second source.
            if extra and (src_dir / extra).exists():
                have = {s["number"] for s in sections}
                toc = _toc(text[:len(text) // 5])
                if extra.endswith(".json"):
                    for s in load_civictech(src_dir / extra):
                        if s["number"] not in have:
                            s["source"] = CIVICTECH
                            sections.append(s)
                else:
                    gazette = pdf_text(src_dir / extra, sized=False)
                    # Where this edition's text doesn't start like the gazette's, it is misplaced
                    # (BNSS s. 58 prints s. 59's text): use the gazette's.
                    official = gazette_sections(gazette, have)
                    for sec in sections:
                        g = official.get(sec["number"])
                        if g and (SequenceMatcher(None, _key_text(sec["text"]), _key_text(g)).ratio() < 0.4
                                  or len(sec["text"]) < 100 < len(g) and "GAZETTE" not in g):
                            sec.update(text=g, source="Gazette of India, 25 December 2023")
                    last = max(int(re.match(r"\d+", n)[0]) for n in have)
                    missing = {str(n) for n in range(1, last + 2)} - have
                    found = gazette_sections(gazette, missing)
                    for n in found:  # "301\nDefinitions." when the contents split the line
                        if n not in toc and (m := re.search(rf"^{n}\.?\s*\n([A-Z][^\n]+)", text, re.M)):
                            toc[n] = m[1].strip(" .")
                    sections += [{"number": n, "title": toc.get(n) or TITLES.get((slug, n), ""), "chapter": None, "text": t,
                                  "source": "Gazette of India, 25 December 2023"} for n, t in found.items()]
                sections.sort(key=lambda s: _num_key(s["number"]))
                for prev, sec in zip(sections, sections[1:]):
                    if sec.get("source"):  # the second source's chapters are numbered differently
                        sec["chapter"] = prev["chapter"]
    return {"slug": slug, "act": name, "short": short, "kind": kind, "source": origin,
            "sections": sections}


def main(src_dir: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for slug, name, short, kind, source, origin, extra in ACTS:
        if not (Path(src_dir) / source).exists():
            print(f"skip {slug}: {source} missing")
            continue
        data = build(slug, name, short, kind, source, origin, extra, Path(src_dir))
        (OUT_DIR / f"{slug}.json").write_text(json.dumps(data, ensure_ascii=False, indent=0) + "\n")
        print(f"{slug}: {len(data['sections'])} {kind}s")


if __name__ == "__main__":
    main(sys.argv[1])
