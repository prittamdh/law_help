"""Pull structured fields out of the text of a Rajasthan High Court judgment PDF.

Rajasthan HC orders share one layout, which the parsers below lean on:

    [2024:RJ-JP:2823]                               <- neutral citation
    HIGH COURT OF JUDICATURE FOR RAJASTHAN ...
    S.B. Civil Writ Petition No. 15117/2023         <- case caption(s)
    <petitioners> ----Petitioner  Versus  <respondents> ----Respondents
    For Petitioner(s) : <advocates>
    For Respondent(s) : <advocates>
    HON'BLE MR. JUSTICE <NAME>                      <- one line per judge
    Judgment / Order
    17/01/2024
    <body>
    (NAME),J                                        <- signature

Everything here is rule based and needs no network or model, so it can be re-run over
stored text whenever the rules improve (bump EXTRACTOR_VERSION when they do).
"""

import io
import re

EXTRACTOR_VERSION = 3

# --------------------------------------------------------------------------- PDF text


# eCourts stamps every page of some PDFs; scanned ones carry nothing else.
_WATERMARK_RE = re.compile(r"^\(Downloaded on .*\)$")


def pdf_text(data: bytes) -> str | None:
    """Extract the text layer with pdfium, then tidy whitespace line by line.

    pdfium is about 25 times faster than pypdf's layout mode and gives the same lines on
    these PDFs, which matters over a million judgments. pypdf is the fallback for the rare
    file pdfium refuses to open.
    """
    try:
        pages = _pdfium_pages(data)
    except Exception:
        pages = _pypdf_pages(data)
    lines = []
    # pdfium gives some hyphens as \x02 ("appellant\x02complainant"), common in Supreme Court PDFs
    for line in "\n".join(pages).replace("\x00", "").replace("\x02", "-").splitlines():
        line = re.sub(r"[ \t\xa0\ufffe\uffff]+", " ", line).strip()
        if line and not _WATERMARK_RE.match(line):
            lines.append(line)
    return _WRAPPED_LABEL_RE.sub(r"For \1 ", "\n".join(lines)) or None


# pdfium keeps a label wrapped in its table cell on lines of its own:
# "For\nRespondent(s)\n: Mr. X" -> "For Respondent(s) : Mr. X"
_WRAPPED_LABEL_RE = re.compile(r"(?m)^For\n([A-Za-z-]+(?:\(s\))?)\n(?=:)")


def _pdfium_pages(data: bytes) -> list[str]:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(data)
    try:
        pages = []
        for page in doc:
            textpage = page.get_textpage()
            pages.append(textpage.get_text_bounded())
            textpage.close()
            page.close()
        return pages
    finally:
        doc.close()


def _pypdf_pages(data: bytes) -> list[str]:
    """pypdf's layout mode; the default mode splits words at kerning gaps ("Dist rict")."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return [page.extract_text(extraction_mode="layout") or "" for page in reader.pages]


# Many Hindi orders are typed in the legacy Kruti Dev font, so their text layer is
# Latin gibberish ("izkFkhZx.k@vfHk;qDrx.k"); `readable` converts it to Unicode Hindi.
_KRUTI_RE = re.compile(r"\b(?:gS|dk|ds|esa|fd;k|vkSj|dh|;g|rFkk|fd)\b")


def language(text: str) -> str:
    """"en", "hi", "hi-krutidev", or "no-text" for a scan with no text layer (needs OCR)."""
    if len(re.findall(r"[A-Za-z\u0900-\u097F]", text)) < 200:
        return "no-text"
    hits = len(_KRUTI_RE.findall(text))
    if hits >= 5 and hits / max(1, len(text.split())) > 0.02:
        return "hi-krutidev"
    # Hindi orders keep an English case header, so judge by the share of Devanagari letters.
    devanagari = len(re.findall(r"[\u0900-\u097F]", text))
    if devanagari >= 100 and devanagari >= 0.2 * len(re.findall(r"[A-Za-z\u0900-\u097F]", text)):
        return "hi"
    return "en"


def readable(text: str | None) -> tuple[str | None, str | None]:
    """(text to store and search, the Kruti Dev original or None): legacy-font Hindi is converted."""
    if text and language(text) == "hi-krutidev":
        from .krutidev import convert_mixed

        return convert_mixed(text), text
    return text, None


# --------------------------------------------------------------------------- layout

NEUTRAL_CITATION_RE = re.compile(r"\[(\d{4}):RJ-(JP|JD):(\d+)(-DB|-FB)?\]|\[(\d{4})/RJ(JP|JD)/(\d+)\]")
_PAGE_HEADER_RE = re.compile(r"^(?:\[[^\]]+\]\s*)?\(\d+ of \d+\).*$")
_CAPTION_RE = re.compile(
    r"^(?:S\.B\.|D\.B\.|F\.B\.|L\.B\.)\s*.+?No\.?\s*[\d/ ]+$|^Civil .*No\.? ?\d+/\d{4}$", re.I)
_SIDES = (r"(?:(?:Non-?)?(?:Accused|Claimants?|Complainant|Plaintiff|Defendant|Objector|Caveator)[- ]?)?"
          r"(Petitioners?|Appellants?|Applicants?|Plaintiffs?|Claimants?|Respondents?|"
          r"Non-?Petitioners?|Defendants?|Opposite Part(?:y|ies))")
_SIDE_RE = re.compile(rf"^-{{2,}}\s*{_SIDES}\b", re.I)
_ADVOCATE_START_RE = re.compile(r"^For\s*(?:the\s*)?([A-Z][A-Za-z-]*(?:\s+Company)?)(?:\(s\))?\s*:\s*(.*)$", re.I)
# Anyone who is not the moving party: the State, an insurer, a caveator.
_RESPONDENT_SIDE_RE = re.compile(r"(?:Respondent|Non|State|Defendant|Insurance|Company|Caveator|Opposite)", re.I)
_TITLE = r"(?:(?:MR|MRS|MS|DR|SHRI|SMT)\.?\s+)"
_JUDGE_RE = re.compile(rf"(?:^|HON\W{{0,2}}BLE\s+)(?:THE\s+)?{_TITLE}*(?:(?:ACTING\s+)?CHIEF\s+)?JUSTICE\s+"
                       rf"{_TITLE}*(?:\(?DR\.?\)?\s+)?([A-Z][A-Z .]+?)\s*$", re.I)
_DATE_LINE_RE = re.compile(r"^(?:Date of .*?:+\s*)?(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$")
_SIGNATURE_RE = re.compile(r"^\((?:DR\.\s*)?[A-Z][A-Z .]+\)\s*,\s*(?:J|CJ|ACTING CJ)\b")


def _clean_lines(text: str) -> list[str]:
    """Drop running page headers and re-join captions and "For ... :" labels that wrap."""
    lines = []
    for ln in text.splitlines():
        if _PAGE_HEADER_RE.match(ln):
            continue
        # "Jodhpur. ----Petitioners": the side marker sometimes shares a line with the address
        lines += [x.strip() for x in re.split(rf"\s+(?=-{{2,}}\s*{_SIDES})", ln, flags=re.I) if x.strip()]
    out: list[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if re.match(r"^(?:S\.B\.|D\.B\.|F\.B\.|L\.B\.)", ln) and not re.search(r"\d+/\d{4}$", ln) \
                and re.search(r"(?:^|No\.?)\s*[\d/ ]*\d+/\d{4}$", nxt):
            ln, i = f"{ln} {nxt}", i + 1
        elif re.match(r"^For\s+\w+\s*:", ln) and re.fullmatch(r"(?:Company|Party|Parties)", nxt):
            ln = re.sub(r"^For\s+(\w+)", lambda m: f"For {m.group(1)} {nxt}", ln)
            i += 1
        elif re.fullmatch(r"For\s*:?\s*(.*)", ln) and re.match(r"^(?:the\s+)?[A-Z][a-z-]+(?:\(s\))?\s*:?\s*$", nxt):
            # "For" / "Respondent(s)" wrapped onto two lines around the colon
            rest = re.fullmatch(r"For\s*:?\s*(.*)", ln).group(1)
            ln, i = f"For {nxt.rstrip(' :')} : {rest}", i + 1
        elif re.fullmatch(r"For\s*:\s*(.*)", ln) and re.match(r"^(?:the\s+)?[A-Z][a-z-]+\(s\)", nxt):
            # "For : Mr. X" / "Respondent(s) Mr. Y"
            m = re.match(r"^((?:the\s+)?[A-Z][a-z-]+\(s\))\s*:?\s*(.*)$", nxt)
            rest = re.fullmatch(r"For\s*:\s*(.*)", ln).group(1)
            ln = f"For {m.group(1)} : {rest}"
            lines[i + 1] = m.group(2)
            if not lines[i + 1]:
                i += 1
        out.append(ln)
        i += 1
    return out


def neutral_citation(text: str) -> str | None:
    m = NEUTRAL_CITATION_RE.search(text[:300])
    if not m:
        return None
    if m.group(1):
        return f"{m.group(1)}:RJ-{m.group(2)}:{m.group(3)}{m.group(4) or ''}"
    return f"{m.group(5)}:RJ-{m.group(6)}:{int(m.group(7))}"


# "Bhudev Singh S/o Late Shri Puran Singh, Aged About 60 Years, R/o ..." -> "Bhudev Singh"
_PARTY_CUT_RE = re.compile(
    r"\s+(?:S/o|D/o|W/o|H/o|C/o|Son\s+Of|Daughter\s+Of|Wife\s+Of|Widow\s+Of|Aged|R/o|B/c|"
    r"Resident|Having|At Present|Presently)\b|,\s*(?:Aged|R/o|Resident|Having|S/o|D/o|W/o)\b|"
    # an organisation's address: "..., Paryatan Bhawan, Iiird Floor, Opposite ..."
    r",\s*(?:[A-Z][a-z]+\s+){0,2}(?:Bhawan|Bhavan|Building|Floor|Plot|House|Opposite|Near|Marg|Road)\b|"
    r",\s*\d+\s*,",
    re.I)


def _party_name(block: str) -> str:
    name = _PARTY_CUT_RE.split(block, maxsplit=1)[0]
    return name.strip(" ,.-") or block.strip()


def _split_parties(lines: list[str]) -> list[str]:
    """Numbered lists ("1. State Of Rajasthan ...") hold several parties."""
    joined = " ".join(lines)
    items = re.split(r"(?:^|\s)\d{1,2}\.\s+(?=[A-Z])", joined)
    return [_party_name(i) for i in items if i.strip()]


def header(text: str) -> dict:
    """Case captions, parties of the lead case, advocates, judges and order date."""
    lines = _clean_lines(text)
    out = {"cases": [], "petitioners": [], "respondents": [],
           "advocates": {"petitioner": [], "respondent": []}, "judges": [], "order_date": None}
    buf: list[str] = []
    lead_done = False
    side_seen = None
    adv_side = None
    body_start = len(lines)
    for i, ln in enumerate(lines):
        if _CAPTION_RE.match(ln) and not out["judges"]:
            out["cases"].append(ln)
            buf = []
            continue
        if re.fullmatch(r"(?:Connected\s+With|With|In|Along\s*With)", ln, re.I) or \
                re.match(r"(?:\[\d{4}|HIGH COURT|BENCH AT|JODHPUR$|JAIPUR$)", ln):
            buf = []
            continue
        m = _SIDE_RE.match(ln)
        if m:
            side = "respondents" if _RESPONDENT_SIDE_RE.match(m.group(1)) else "petitioners"
            if not lead_done:
                out[side] = _split_parties(buf)
                if side == "respondents":
                    lead_done = True
            side_seen = side
            buf = []
            continue
        if ln.lower() == "versus" or ln.lower() == "vs.":
            continue
        m = _ADVOCATE_START_RE.match(ln)
        if m:
            adv_side = "respondent" if _RESPONDENT_SIDE_RE.match(m.group(1)) else "petitioner"
            if m.group(2):
                out["advocates"][adv_side].append(m.group(2))
            continue
        m = _JUDGE_RE.search(ln) if "JUSTICE" in ln.upper() else None
        if m:
            out["judges"].append(m.group(1).strip().upper())
            adv_side = None
            continue
        if out["judges"]:
            if re.fullmatch(r"(?:(?:Judgment|Order|Judgment\s*/\s*Order)(?:\s*\(Oral\))?|Reportable|"
                            r"Date of .*|Reserved.*|Pronounced.*|Per .*|\(.*\))", ln, re.I) \
                    and not _DATE_LINE_RE.match(ln):
                continue
            m = _DATE_LINE_RE.match(ln)
            if m and out["order_date"] is None:
                out["order_date"] = f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
                continue
            body_start = i
            break
        if adv_side:
            out["advocates"][adv_side].append(ln)
            continue
        if side_seen is None or buf or not lead_done:
            buf.append(ln)
    for side in ("petitioner", "respondent"):
        out["advocates"][side] = _split_advocates(out["advocates"][side])
    out["_body"] = "\n".join(lines[body_start:])
    return out


def _split_advocates(entries: list[str]) -> list[str]:
    names = []
    for e in entries:
        # "Mr. A for Mr. B" (A appearing on B's behalf), "Mr. A, AAG assisted by Mr. B"
        for part in re.split(r"\s+(?:with|&|and|for|assisted(?:\s+by)?)\s+|;\s*|\s{2,}|"
                             r"\s+(?:with|for|assisted|assisted\s+by|by)$|^by\s+", e):
            part = (part or "").strip(" ,.")
            if re.match(r"(?:Mr|Mrs|Ms|Dr|Shri|Smt|Km)\b", part, re.I) or (part and names == []):
                names.append(part)
            elif part and names:
                names[-1] = f"{names[-1]}, {part}"   # a trailing designation like "PP"
    return [n for n in names if n and not re.match(r"(?:none|nemo|nil)\b|-+$", n, re.I)]


def body(text: str) -> str:
    """Judgment body without the header, page headers and signature block."""
    lines = header(text)["_body"].splitlines()
    for i, ln in enumerate(lines):
        if _SIGNATURE_RE.match(ln):
            lines = lines[:i]
            break
    return "\n".join(lines)


# --------------------------------------------------------------------------- acts and sections

# Short forms seen in Rajasthan HC orders, mapped to one canonical name per act.
ACT_ALIASES: list[tuple[str, str]] = [
    (r"I\.?\s?P\.?\s?C\.?|(?:Indian )?Penal Code(?:,? 1860)?", "Indian Penal Code, 1860"),
    (r"Cr\.?\s?P\.?\s?C\.?|Code of Criminal Procedure(?:,? 1973)?", "Code of Criminal Procedure, 1973"),
    (r"C\.?\s?P\.?\s?C\.?|Code of Civil Procedure(?:,? 1908)?", "Code of Civil Procedure, 1908"),
    (r"B\.?\s?N\.?\s?S\.?\s?S\.?|Bharatiya Nagarik Suraksha Sanhita(?:,? 2023)?",
     "Bharatiya Nagarik Suraksha Sanhita, 2023"),
    (r"B\.?\s?N\.?\s?S\.?|Bharatiya Nyaya Sanhita(?:,? 2023)?", "Bharatiya Nyaya Sanhita, 2023"),
    (r"B\.?\s?S\.?\s?A\.?|Bharatiya Sakshya Adhiniyam(?:,? 2023)?", "Bharatiya Sakshya Adhiniyam, 2023"),
    (r"N\.?\s?D\.?\s?P\.?\s?S\.?(?: Act)?|Narcotic Drugs and Psychotropic Substances Act(?:,? 1985)?",
     "Narcotic Drugs and Psychotropic Substances Act, 1985"),
    (r"N\.?\s?I\.? Act|Negotiable Instruments? Act(?:,? 1881)?", "Negotiable Instruments Act, 1881"),
    (r"(?:Indian )?Evidence Act(?:,? 1872)?", "Indian Evidence Act, 1872"),
    (r"Constitution(?: of India)?", "Constitution of India"),
    (r"POCSO(?: Act)?|Protection of Children from Sexual Offences Act(?:,? 2012)?",
     "Protection of Children from Sexual Offences Act, 2012"),
    (r"SC/?ST(?: \(Prevention of Atrocities\))? Act|Scheduled Castes and (?:the )?Scheduled Tribes "
     r"\(Prevention of Atrocities\) Act(?:,? 1989)?",
     "Scheduled Castes and Scheduled Tribes (Prevention of Atrocities) Act, 1989"),
    (r"I\.?\s?T\.? Act|Information Technology Act(?:,? 2000)?", "Information Technology Act, 2000"),
    (r"Arms Act(?:,? 1959)?", "Arms Act, 1959"),
    (r"(?:Rajasthan )?Excise Act(?:,? 1950)?", "Rajasthan Excise Act, 1950"),
    (r"M\.?\s?V\.? Act|Motor Vehicles Act(?:,? 1988)?", "Motor Vehicles Act, 1988"),
    (r"Dowry Prohibition Act(?:,? 1961)?", "Dowry Prohibition Act, 1961"),
    (r"(?:P\.?C\.? Act|Prevention of Corruption Act(?:,? 1988)?)", "Prevention of Corruption Act, 1988"),
    (r"(?:J\.?J\.? Act|Juvenile Justice \(Care and Protection of Children\) Act(?:,? 2015)?)",
     "Juvenile Justice (Care and Protection of Children) Act, 2015"),
    (r"Limitation Act(?:,? 1963)?", "Limitation Act, 1963"),
    (r"Arbitration (?:and|&) Conciliation Act(?:,? 1996)?", "Arbitration and Conciliation Act, 1996"),
    (r"Industrial Disputes Act(?:,? 1947)?", "Industrial Disputes Act, 1947"),
    (r"Hindu Marriage Act(?:,? 1955)?", "Hindu Marriage Act, 1955"),
    (r"Specific Relief Act(?:,? 1963)?", "Specific Relief Act, 1963"),
    (r"Payment of Gratuity Act(?:,? 1972)?", "Payment of Gratuity Act, 1972"),
    (r"Right to Information Act(?:,? 2005)?|RTI Act", "Right to Information Act, 2005"),
    (r"Trade Unions Act(?:,? 1926)?", "Trade Unions Act, 1926"),
    (r"General Clauses Act(?:,? 1897)?", "General Clauses Act, 1897"),
    (r"Income[- ]Tax Act(?:,? 1961)?", "Income Tax Act, 1961"),
    (r"Commercial Courts Act(?:,? 2015)?", "Commercial Courts Act, 2015"),
    (r"Companies Act(?:,? 2013)", "Companies Act, 2013"),
    (r"(?:Rajasthan )?Land Revenue Act(?:,? 1956)?", "Rajasthan Land Revenue Act, 1956"),
    (r"(?:Rajasthan )?Tenancy Act(?:,? 1955)?", "Rajasthan Tenancy Act, 1955"),
    (r"(?:Rajasthan )?Municipalities Act(?:,? 2009)?", "Rajasthan Municipalities Act, 2009"),
    (r"(?:Rajasthan )?Panchayati Raj Act(?:,? 1994)?", "Rajasthan Panchayati Raj Act, 1994"),
    (r"Land Acquisition Act(?:,? 1894)?", "Land Acquisition Act, 1894"),
    (r"Transfer of Property Act(?:,? 1882)?", "Transfer of Property Act, 1882"),
    (r"(?:Indian )?Contract Act(?:,? 1872)?", "Indian Contract Act, 1872"),
    (r"Hindu Succession Act(?:,? 1956)?", "Hindu Succession Act, 1956"),
    (r"Protection of Women from Domestic Violence Act(?:,? 2005)?|D\.?V\.? Act",
     "Protection of Women from Domestic Violence Act, 2005"),
]
_ALIAS_RES = [(re.compile(rf"^(?:the\s+)?(?:{pat})$", re.I), name) for pat, name in ACT_ALIASES]
_ALIAS_ANY = "|".join(f"(?:{pat})" for pat, _ in ACT_ALIASES)

# A full statute name: "Rajasthan Tenancy Act, 1955", "Rajasthan Civil Services (Pension) Rules, 1996".
# Case sensitive even inside re.I patterns: it must start with a capitalised word.
_STATUTE = (r"(?-i:[A-Z][A-Za-z.'&/-]*"
            r"(?:\s+(?:[A-Z][A-Za-z.'&/-]*|\([A-Za-z ,&']+\)|of|the|and|for|to|in|&)){0,10}?"
            r"\s+(?:Act|Rules|Regulations|Code|Manual|Ordinance|Sanhita|Adhiniyam)(?:,?\s*\d{4})?)")

# 302, 120-B, 498A, 14 A(2), 3(1)(r): a suffix is one capital letter that does not start a word
_SEC_NUM = r"\d{1,4}(?-i:\s?-?\s?[A-Z](?![A-Za-z]|\.[A-Za-z]))?(?:\s?\(\s?[0-9a-z]{1,4}\s?\))*"
_SEC_LIST = rf"{_SEC_NUM}(?:\s*(?:,|/|&|and|r/w|read with|or|to)\s*{_SEC_NUM})*"
_PROVISION_RE = re.compile(  # "Sections 420, 467 and 120-B of IPC", "u/s 8/15 NDPS Act"
    rf"\b(?P<kind>Order\s*[IVXL\d]+\s*,?\s*Rules?|Sections?|Secs?\.?|S\.|u/s\.?|under section|"
    rf"Articles?|Art\.|Rules?)\s*"
    rf"(?P<nums>{_SEC_LIST})"
    rf"(?:,?\s*(?:(?:of|under|in)\s+)?(?:the\s+)?(?P<act>{_ALIAS_ANY}|{_STATUTE})(?![A-Za-z]))?",
    re.I,
)
# Offence lists often skip the word "Section": "under 143, 341 & 120-B IPC", "457 IPC".
_SHORT_CODES = (r"I\.?\s?P\.?\s?C\.?|Cr\.?\s?P\.?\s?C\.?|B\.?\s?N\.?\s?S\.?\s?S?\.?|"
                r"(?:N\.?\s?D\.?\s?P\.?\s?S\.?|POCSO|SC/ST|N\.?\s?I\.?) Act")
_BARE_RE = re.compile(rf"(?<![\w/.,-])(?P<nums>{_SEC_LIST})\s+(?:of\s+(?:the\s+)?)?(?P<act>{_SHORT_CODES})(?![A-Za-z])", re.I)
# An act named with no provision: "pertains to Right To Information Act, 2005".
_ACT_MENTION_RE = re.compile(
    rf"(?P<act>(?-i:[A-Z]{{2,6}}(?:/[A-Z]{{2}})? Act)|{_STATUTE})(?!\s*\)?\s*(?:Cases|Court))")
_NUM_RE = re.compile(_SEC_NUM, re.I)


def canonical_act(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip(" ,.")
    for rx, canon in _ALIAS_RES:
        if rx.match(name):
            return canon
    return re.sub(r"^the\s+", "", name, flags=re.I)


def _flatten(text: str) -> str:
    # Re-join words hyphenated across lines and let sentences run across line breaks.
    return re.sub(r"\s*\n\s*", " ", re.sub(r"-\n(?=[a-z])", "", text))


def acts_cited(text: str) -> list[dict]:
    """[{"act": "Indian Penal Code, 1860", "sections": ["354", "376"]}, ...]

    A provision with no act named next to it ("under Section 439") is left out rather than
    guessed, except that an Article with no act is taken to be the Constitution.
    """
    found: dict[str, set[str]] = {}
    flat = _flatten(text)
    matches = list(_PROVISION_RE.finditer(flat))
    covered = [m.span() for m in matches]
    matches += [m for m in _BARE_RE.finditer(flat)
                if not any(s <= m.start() < e for s, e in covered) and not re.match(r"\d+/\d{4}", m.group("nums"))]
    for m in matches:
        kind = (m.groupdict().get("kind") or "section").lower()
        act = m.group("act")
        if not act:
            if kind.startswith("art"):
                act = "Constitution"
            else:
                continue
        act = canonical_act(act)
        if re.fullmatch(r"(?:Act|Rules|Regulations|Code|Ordinance|Order|Sanhita|Adhiniyam)(?:,? \d{4})?", act):
            continue
        nums = [re.sub(r"[\s-]+", "", n) for n in _NUM_RE.findall(m.group("nums"))]
        if kind.startswith("order"):
            order = re.search(r"[IVXL\d]+", m.group("kind")[5:], re.I).group().upper()
            nums = [f"Order {order} Rule {n}" for n in nums]
        elif kind.startswith("rule"):
            nums = [f"Rule {n}" for n in nums]
        found.setdefault(act, set()).update(nums)
    for m in _ACT_MENTION_RE.finditer(flat):
        act = canonical_act(m.group("act"))
        if not re.fullmatch(r"(?:the\s+)?(?:Act|Rules|Regulations|Code|Ordinance|Order|Sanhita|Adhiniyam)(?:,? \d{4})?", act, re.I) \
                and (act != m.group("act").strip() or re.search(r"\d{4}$", act)
                     or re.fullmatch(r"(?:[A-Z][a-z]+ ){2,}Act", act)):
            # a well-known short form, a name with a year, or "Commercial Courts Act"
            found.setdefault(act, set())
    return [{"act": a, "sections": sorted(s, key=_section_key)} for a, s in sorted(found.items())]


def _section_key(s: str):
    m = re.search(r"\d+", s)
    return (s[:1].isalpha(), int(m.group()) if m else 0, s)


# --------------------------------------------------------------------------- cases cited

# Reporter citations common in Rajasthan HC orders.
_REPORTERS = [
    r"AIR\s*\d{4}\s*(?:SC|S\.C\.|Raj(?:asthan)?|[A-Z][a-z]+)\s*\d+",
    r"\(\d{4}\)\s*\d+\s*SCC\s*(?:\(Cri\)\s*)?\d+",
    r"\d{4}\s*SCC\s*[Oo]n[Ll]ine\s*[A-Z][A-Za-z]*\s*\d+",
    r"\d{4}\s*INSC\s*\d+",
    r"\(\d{4}\)\s*\d+\s*SCR\s*\d+",
    r"\d{4}\s*\(\d+\)\s*SCR\s*\d+",
    r"JT\s*\d{4}\s*\(\d+\)\s*SC\s*[-–]?\s*\d+",
    r"\d{4}\s*\(\d+\)\s*(?:RLW|WLC|RLR|RCR|Cr\.?\s?L\.?\s?R\.?)\s*(?:\((?:Raj|SC|Rajasthan)\)\s*)?\d+",
    r"\d{4}\s*Cr\.?\s?L\.?\s?J\.?\s*(?:\(SC\)\s*)?\d+",
    r"\d{4}\s*(?:\(\d+\)\s*)?(?:ACJ|ACC|DNJ|RAR|TAC)\s*\d+",
    r"\d{4}:RJ-(?:JP|JD):\d+(?:-DB|-FB)?",
]
CITATION_RE = re.compile("|".join(f"(?:{r})" for r in _REPORTERS))

_NAME_WORD = r"(?:\(?[A-Z][\w.'’&()/-]*|of|the|and|&|for|through|thr\.|@|\(|\))"
_CASE_NAME_RE = re.compile(
    rf"(?P<a>(?:{_NAME_WORD}\s+){{0,7}}?[A-Z][\w.'’&()/-]*),?\s+"
    rf"(?:v\.|V\.|vs\.?|Vs\.?|VS\.?|versus|Versus)\s+"
    rf"(?P<b>[A-Z][\w.'’/-]*(?:\s+{_NAME_WORD}){{0,8}})"
)
# Words that open a sentence rather than a case name ("In Gian Singh v. ...").
_LEAD_JUNK = re.compile(
    r"^(?:(?:In|in|the|The|case|Case|of|matter|Matter|titled|as|As|judgment|Judgment|Hon'?ble|"
    r"Hon’ble|Supreme|Court|Apex|decision|Decision|rendered|reported|See|see|also|Also|and|"
    r"vide|Vide|Division|Bench|Full|Larger|Constitution|This|this|by|By|Learned|counsel|relied|"
    r"upon|on|On|placed|reliance|cited|Petition|No\.|dated|re|Re|between|Between|said|aforesaid|"
    r"where|Where|wherein|Wherein|while|While|a|A|an|An|that|That|from|From|with|With)\s+)+")
_TAIL_JUNK = re.compile(
    r"(?:\s+(?:reported|in|decided|dated|and|the|of|,|wherein|which|where|has|have|had|is|was|"
    r"passed|Civil|Criminal|Writ|Petition|Appeal|Special|Leave|D\.B\.|S\.B\.|SLP|No\.?|Hon'?ble|"
    r"Supreme|Court|&))+$")


def _first_unclosed(s: str) -> int:
    stack = []
    for i, ch in enumerate(s):
        if ch == "(":
            stack.append(i)
        elif ch == ")" and stack:
            stack.pop()
    return stack[0] if stack else len(s)


def _tidy_name(s: str, lead: bool) -> str:
    s = s.strip(" ,:;-(")
    if lead:
        # "(ii) Santdas ...", "No.6863/2014 (Kheta Ram ...": keep what follows a stray bracket
        if s.count("(") > s.count(")"):
            s = s[s.rindex("(") + 1:]
        elif s.count(")") > s.count("(") and s.index(")") < len(s) - 1:
            s = s[s.index(")") + 1:]
        s = _LEAD_JUNK.sub("", s.strip())
    else:
        s = s[:_first_unclosed(s)]                # "... & Ors. (Civil Appeal No."
        s = re.sub(r"\s*\([^()]*\bNo\b[^()]*\)\W*$", "", s)   # "... (S.B. ... No.11530/2023)"
    s = _TAIL_JUNK.sub("", s)
    s = re.sub(r"\((?:supra)\)", "", s, flags=re.I).strip(" ,:;-(")
    if s.count(")") > s.count("("):
        s = s.rstrip(").")                         # "... & Ors.)." closing an earlier bracket
    return s.strip()


def cases_cited(text: str, own_citation: str | None = None) -> list[dict]:
    """[{"name": "Gian Singh v. State of Punjab", "citations": ["2012 Cr.L.J. (SC) 4934"]}, ...]

    Case names are matched as "<A> vs <B>". A reporter citation within 200 characters after
    the name is attached to it; reporter citations with no name nearby are kept on their own.
    """
    flat = _flatten(text)
    cases: list[dict] = []
    used_spans: list[tuple[int, int]] = []
    for m in _CASE_NAME_RE.finditer(flat):
        a, b = _tidy_name(m.group("a"), lead=True), _tidy_name(m.group("b"), lead=False)
        if not a or not b or len(a) < 3 or len(b) < 3:
            continue
        window = flat[m.end():m.end() + 200]
        cut = _CASE_NAME_RE.search(window)
        if cut:
            window = window[:cut.start()]
        cites = []
        for c in CITATION_RE.finditer(window):
            cites.append(_norm_cite(c.group()))
            used_spans.append((m.end() + c.start(), m.end() + c.end()))
        cases.append({"name": f"{a} v. {b}", "citations": cites})
    for c in CITATION_RE.finditer(flat):
        if any(s <= c.start() < e for s, e in used_spans):
            continue
        cases.append({"name": None, "citations": [_norm_cite(c.group())]})

    own = own_citation.replace("-DB", "").replace("-FB", "") if own_citation else None
    merged: dict[str, dict] = {}
    for c in cases:
        c["citations"] = [x for x in c["citations"]
                          if not own or x.replace("-DB", "").replace("-FB", "") != own]
        if not c["name"] and not c["citations"]:
            continue
        key = _case_key(c["name"]) if c["name"] else c["citations"][0]
        if key in merged:
            for x in c["citations"]:
                if x not in merged[key]["citations"]:
                    merged[key]["citations"].append(x)
        else:
            merged[key] = c
    # a bare citation that also appears under a named case is a duplicate
    named = {x for c in merged.values() if c["name"] for x in c["citations"]}
    return [c for c in merged.values() if c["name"] or c["citations"][0] not in named]


def _norm_cite(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _case_key(name: str) -> str:
    """Same parties, however spelled: "Gian Singh Vs. State of Punjab & Anr" twice."""
    a, _, b = name.partition(" v. ")
    words = lambda x: re.sub(r"[^a-z]+", " ", x.lower()).split()
    stop = {"the", "of", "and", "ors", "anr", "state", "union", "india", "rajasthan", "dr", "smt", "shri",
            "reported", "in"}
    key = lambda x: " ".join([w for w in words(x) if w not in stop][:2]) or " ".join(words(x)[:3])
    return f"{key(a)}|{key(b)}"


# --------------------------------------------------------------------------- this court's cases

# Rajasthan HC orders cite the court's own unreported orders by case number:
#   "Kheta Ram v. State (S.B. Civil Writ Petition No.6863/2014) decided on 12.03.2015".
_CASE_NO_RE = re.compile(
    r"\b([SDFL])\.\s?B\.\s*([A-Za-z][A-Za-z .()/&-]{2,80}?)\s*No\.?\s*:?\s*(\d{1,6})\s*/\s*((?:19|20)\d{2})\b")

# How a caption's case kind maps to the eCourts case type codes, first match wins.
# Some kinds have had more than one code over the years: those list each.
_CASE_KINDS: list[tuple[str, str]] = [
    (r"bail cancel", "CRLBC"),
    (r"suspension of sentence", "SOSA"),
    (r"bail", "CRLMB"),
    (r"habeas", "HC"),
    (r"special (?:appeal|writ)|spl\.? ?appl?\.?|\bs\.?\s?a\.?\s?w\b", "SAW"),
    (r"criminal contempt", "CRLCP"),
    (r"contempt", "CCP"),
    (r"review.*writ|writ.*review", "WRW"),
    (r"restoration.*writ|writ.*restoration", "WRES"),
    (r"restoration", "CRES"),
    (r"writ misc", "WMAP"),
    (r"criminal writ", "CRLW"),
    (r"civil writ|writ petition|^c\.?\s?w\.?(?:\s?p\.?)?$", "CW"),
    (r"(?:criminal|crl?\.?) misc.*(?:petition|\(p\))", "CRLMP"),
    (r"(?:criminal|crl?\.?) misc.*application", "CRLMA"),
    (r"criminal revision", "CRLR"),
    (r"civil revision", "CR"),
    (r"leave to appeal", "CRLLA"),
    (r"criminal appeal", "CRLAS|CRLAD|CRLA"),
    (r"civil misc.*appeal", "CMA"),
    (r"civil misc.*application", "CMAP"),
    (r"first appeal", "CFA"),
    (r"second appeal", "CSA"),
    (r"company application", "COAP"),
    (r"company petition", "COP"),
    (r"arbitration application", "ARBAP"),
    (r"income tax appeal", "ITA"),
    (r"sales tax revision", "STR"),
]
_CASE_KIND_RES = [(re.compile(p, re.I), code) for p, code in _CASE_KINDS]

# A case number is a citation when it names a decided case, not when it lists a connected
# matter or says which case counsel appears in.
_CITED_NEAR_RE = re.compile(r"\b(?:v\.|vs\.?|versus|v/s)\s|decided|judgment|order dated|dated", re.I)
_NOT_CITED_RE = re.compile(
    r"(?:appearing|appears|appeared|along with|alongwith|analogous|connected|tagged|restored|"
    r"restoration of(?: the)?|pleadings and prayers made in|pending)\W*(?:in\W*)?$", re.I)


def case_kind(words: str, bench: str) -> str | None:
    """"Civil Writ Petition" -> "CW"; "Criminal Appeal" at a single bench -> "CRLAS|CRLA"."""
    words = re.sub(r"\s+", " ", words).strip()
    for rx, code in _CASE_KIND_RES:
        if rx.search(words):
            if code == "CRLAS|CRLAD|CRLA":
                return "CRLAD|CRLA" if bench == "D" else "CRLAS|CRLA"
            return code
    return None


def case_refs(body_text: str, own: set[str] | frozenset = frozenset(),
              cases_cited_: list[dict] | None = None) -> list[str]:
    """This court's cases the judgment cites, as "TYPE/NO/YEAR/<party words>/<bench>" refs:

        ["CW/6863/2014/kheta/", "CRLAS|CRLA/12/2020//jodhpur", "NC:2024:RJ-JP:2823"]

    TYPE may list more than one case type code. Jaipur and Jodhpur number their cases
    separately, so the same number can be two cases: the party words (from the case name
    next to the number) and the bench (when the text names the seat) tell them apart.
    `own` holds the "TYPE/NO/YEAR" refs of the judgment's own and connected cases, which are
    skipped. Neutral citations come from `cases_cited_` (already parsed from the same text).
    """
    flat = _flatten(body_text)
    refs: dict[str, str] = {}
    for m in _CASE_NO_RE.finditer(flat):
        kind = case_kind(m.group(2), m.group(1))
        if not kind:
            continue
        key = f"{kind}/{int(m.group(3))}/{m.group(4)}"
        if key in refs or any(o in own for o in _ref_variants(key)):
            continue
        before, after = flat[max(0, m.start() - 200):m.start()], flat[m.end():m.end() + 150]
        if _NOT_CITED_RE.search(before[-60:]):
            continue
        if not (_CITED_NEAR_RE.search(before) or _CITED_NEAR_RE.search(after)):
            continue
        refs[key] = f"{key}/{' '.join(_party_words(_nearby_name(before, after)))}/{_seat(before, after)}"
    out = list(refs.values())
    for c in cases_cited_ or []:
        for x in c["citations"]:
            m = re.fullmatch(r"(\d{4}:RJ-(?:JP|JD):\d+)(?:-DB|-FB)?", x)
            if m and f"NC:{m.group(1)}" not in out:
                out.append(f"NC:{m.group(1)}")
    return out


def _nearby_name(before: str, after: str) -> str | None:
    """The "A v. B" case name right after a case number ("(titled as A Vs. B)", ": A Vs. B") or
    right before it ("A Vs. B (S.B. Civil Writ ...", "A Vs. B : S.B. ...")."""
    m = _CASE_NAME_RE.search(after[:120])
    if m and m.start() <= 25:
        return f"{m.group('a')} {m.group('b')}"
    last = None
    for m in _CASE_NAME_RE.finditer(before):
        last = m
    if last and len(before) - last.end() <= 40:
        return f"{last.group('a')} {last.group('b')}"
    return None


# Words that don't tell two cases apart: the State, legal filler, the commonest name parts.
_COMMON_WORDS = set("""
    the of and in to for by as at on or v vs versus titled case matter anr ors others another
    state rajasthan union india govt government through its thr secretary principal chief director
    officer collector district municipal board corporation ltd pvt limited company m s
    smt shri sh dr mr mrs ms km kumari late son daughter wife s o d o w o
    singh kumar lal ram chand devi bai prasad
    sb db civil criminal writ petition appeal special application misc miscellaneous no bail
    reported decided dated order judgment supra raj rajashtan hence
""".split())


def _party_words(name: str | None) -> list[str]:
    if not name:
        return []
    words = re.findall(r"[a-z]{3,}", name.lower())
    return list(dict.fromkeys(w for w in words if w not in _COMMON_WORDS))[:4]


def _seat(before: str, after: str) -> str:
    """"jodhpur" or "jaipur" when the text says which seat decided the cited case."""
    # the same sentence only: "... No.812/2024. 3. The Principal Seat at Jodhpur ..." is another case
    brk = r"\.\s+(?=\d+\.\s|[A-Z])"
    near = f"{re.split(brk, before[-120:])[-1]} {re.split(brk, after[:80])[0]}".lower()
    if re.search(r"principal seat|at jodhpur|bench at jodhpur", near):
        return "jodhpur"
    if re.search(r"jaipur bench|bench at jaipur|at jaipur", near):
        return "jaipur"
    return ""


def _ref_variants(ref: str) -> list[str]:
    kinds, number, year = ref.split("/")[:3]
    return [f"{k}/{number}/{year}" for k in kinds.split("|")]


def own_refs(captions: list[str]) -> set[str]:
    """The judgment's own and connected cases, from its header captions, as "TYPE/NO/YEAR" refs."""
    own = set()
    for cap in captions:
        for m in _CASE_NO_RE.finditer(cap):
            kind = case_kind(m.group(2), m.group(1))
            if kind:
                own.update(_ref_variants(f"{kind}/{int(m.group(3))}/{m.group(4)}"))
    return own


# --------------------------------------------------------------------------- summary

_PURPOSE_RE = re.compile(
    r"\b(?:preferred|filed|moved|instituted|submitted|under Section|under Article|challenging|"
    r"seeking|praying|prayer|assail|laid|directed against|arises? out of|has been)\b", re.I)
_OUTCOME_RE = re.compile(
    r"\b(?:allowed|dismissed|quashed|set aside|granted|rejected|withdrawn|accepted|"
    r"released on bail|enlarged on bail|suspended|not inclined)\b", re.I)
_DISPOSED_RE = re.compile(r"\bdisposed\b", re.I)


# Abbreviations that end in a full stop without ending the sentence.
_ABBREV = {"vs", "v", "no", "nos", "ors", "anr", "dist", "distt", "mr", "mrs", "ms", "dr", "sh",
           "smt", "shri", "st", "sr", "jr", "govt", "dy", "addl", "asstt", "asst", "hon'ble",
           "hon’ble", "ltd", "pvt", "co", "viz", "etc", "i.e", "e.g", "u/s", "p.s", "sec", "art",
           "f.i.r", "r/o", "s/o", "d/o", "w/o", "rs", "para", "paras"}


def _sentences(text: str) -> list[str]:
    flat = re.sub(r"\s+", " ", _flatten(text))
    out, start = [], 0
    for m in re.finditer(r"[.?!]\s+(?=(?:\d+\.\s+)?[A-Z“\"(])", flat):
        prev = flat[start:m.start()].rsplit(" ", 1)[-1].lower().strip("(“\"")
        if prev in _ABBREV or re.fullmatch(r"[a-z]|[a-z](?:\.[a-z])+|\d+", prev):
            continue  # "vs.", "No.", an initial like "K." or a numbered list item
        out.append(flat[start:m.start() + 1])
        start = m.end()
    out.append(flat[start:])
    return [re.sub(r"^\d+\.\s*", "", p).strip() for p in out if len(p.strip()) > 20]


# Closing lines that tidy up pending applications say nothing about the case itself.
_BOILERPLATE_RE = re.compile(r"\b(?:pending application|stay application|stay petition|"
                             r"misc(?:ellaneous)?\.? application|record be sent|office to proceed|"
                             r"consequences? to follow)", re.I)


def summary(body_text: str, max_chars: int = 600) -> str | None:
    """A two-sentence extractive summary: what was sought, and what the court did.

    This is a stand-in for a model-written summary; it copies the court's own words.
    """
    sents = _sentences(body_text)
    if not sents:
        return None
    purpose = next((s for s in sents[:6] if _PURPOSE_RE.search(s)), sents[0])
    tail = [s for s in reversed(sents[-8:]) if not _BOILERPLATE_RE.search(s)]
    # "disposed of" is the weakest outcome: an "allowed" or "dismissed" line says more
    outcome = next((s for s in tail if _OUTCOME_RE.search(s)), None) or \
        next((s for s in tail if _DISPOSED_RE.search(s)), None)
    parts = [purpose] if outcome in (None, purpose) else [purpose, outcome]
    out = []
    for p in parts:
        p = p if len(p) <= max_chars // len(parts) else p[:max_chars // len(parts) - 1].rsplit(" ", 1)[0] + "…"
        out.append(p.rstrip(".") + ".")
    return " ".join(out)


# --------------------------------------------------------------------------- outcome, reasoning, headline

# The court's operative words, most specific first. Checked against the closing sentences.
_OUTCOMES: list[tuple[str, re.Pattern]] = [(label, re.compile(rx, re.I)) for label, rx in [
    ("Withdrawn", r"\b(?:dismissed|disposed of)\s+as\s+(?:having been\s+)?withdrawn|\bpermitted to withdraw|"
                  r"\bis\s+(?:hereby\s+)?withdrawn"),
    ("Dismissed as infructuous", r"\binfructuous\b"),
    ("Dismissed for non-prosecution", r"\bdismissed\b[^.]{0,40}\b(?:in default|for non[- ]?prosecution)"),
    ("Leave to appeal granted", r"\bleave to appeal is (?:hereby )?granted"),
    ("Bail granted", r"\b(?:released|enlarged) on (?:regular |anticipatory )?bail|\bbail application\b[^.]{0,60}\ballowed\b"),
    ("Bail refused", r"\bbail application\b[^.]{0,60}\b(?:dismissed|rejected)\b"),
    ("Sentence suspended", r"\bsentence\b[^.]{0,80}\bsuspended\b"),
    ("Partly allowed", r"\b(?:partly|partially)\s+allowed|\ballowed\s+(?:in part|partly)"),
    ("Proceedings quashed", r"\b(?:FIR|F\.I\.R\.?|proceedings?|charge[- ]?sheet|complaint|cognizance)\b[^.]{0,200}"
                            r"\bquashed|\bquashed\b[^.]{0,80}\b(?:FIR|F\.I\.R|proceedings)\b"),
    ("Allowed", r"\b(?:is|are|stands?|hereby|accordingly)\s+(?:also\s+)?(?:hereby\s+)?allowed\b"),
    ("Dismissed", r"\b(?:is|are|stands?|hereby|accordingly)\s+(?:also\s+)?(?:hereby\s+)?(?:dismissed|rejected)\b"),
    ("Remanded", r"\bremanded?\b|\bremitted back\b"),
    ("Disposed of", r"\bdisposed of\b"),
]]


def outcome(body_text: str) -> str | None:
    """What the court did, as a short label ("Bail granted", "Dismissed"), from the closing lines.

    The last sentence that states an outcome wins: earlier ones are often the court quoting
    an argument or another case.
    """
    for sent in reversed(_sentences(body_text)[-10:]):
        if _BOILERPLATE_RE.search(sent):
            continue
        for label, rx in _OUTCOMES:
            if rx.search(sent):
                return label
    return None


# Words a judge uses when giving reasons, and words that mark a party's argument instead.
_REASONING_RE = re.compile(
    r"\b(?:(?:in|to) (?:my|our) (?:considered )?(?:view|opinion)|(?:this court|we|i) (?:am|are|is) "
    r"(?:of the|of the considered) (?:view|opinion)|considered (?:view|opinion)|having (?:heard|considered|"
    r"perused|gone through)|in (?:the )?(?:light|view) of (?:the )?(?:above|aforesaid|foregoing)|"
    r"it is (?:thus |therefore )?(?:clear|evident|apparent|well settled|settled)|the law is (?:well )?settled|"
    r"(?:question|issue) (?:that arises|for consideration|which arises)|(?:therefore|thus|hence),? "
    r"(?:this court|we|it)|this court (?:finds|holds|is satisfied)|no (?:merit|substance)|"
    r"(?:we|this court) (?:find|hold|are satisfied))\b", re.I)
_ARGUMENT_RE = re.compile(r"\b(?:counsel|learned (?:PP|AAG|AG|GA)|petitioner|appellant|respondent)s?\b"
                          r"[^.]{0,80}\b(?:submit|submits|submitted|contend|contends|contended|argue|"
                          r"argues|argued|urged|pointed out)\b", re.I)


def key_reasoning(body_text: str, min_body: int = 8000, max_chars: int = 900) -> str | None:
    """For a long judgment, the three consecutive sentences that most read like the court's reasons.

    Short orders say what they decide in the summary already, so they get nothing here.
    """
    if len(body_text) < min_body:
        return None
    sents = _sentences(body_text)
    if len(sents) < 12:
        return None
    score = [len(_REASONING_RE.findall(s)) - 2 * bool(_ARGUMENT_RE.search(s)) - bool(_BOILERPLATE_RE.search(s))
             for s in sents]
    # Reasons come after the facts and arguments: skip the first third, and the closing order.
    start, end = len(sents) // 3, max(len(sents) // 3 + 3, len(sents) - 3)
    windows = [(sum(score[i:i + 3]), -i) for i in range(start, end - 2)]
    if not windows:
        return None
    best, neg_i = max(windows)
    if best < 2:
        return None
    text = re.sub(r"\s+\d+(?:\.\d+)*\.?$", "", " ".join(sents[-neg_i:-neg_i + 3]))  # a trailing "11.1."
    if len(text) > max_chars:
        text = text[:max_chars - 1].rsplit(" ", 1)[0] + "…"
    return text


# Rajasthan HC case type codes, for the headline. Codes not listed are shown as they are.
CASE_KINDS = {
    "CW": "Writ petition", "CRLMB": "Bail application", "CRLMP": "Criminal misc. petition",
    "CRLW": "Criminal writ petition", "CMA": "Civil misc. appeal", "CRLR": "Criminal revision",
    "SOSA": "Suspension of sentence", "SOSR": "Suspension of sentence", "CCP": "Contempt petition",
    "CRLAS": "Criminal appeal", "CRLA": "Criminal appeal", "CRLAD": "Criminal appeal",
    "SAW": "Special appeal (writ)", "HC": "Habeas corpus", "CFA": "First appeal",
    "CRLLA": "Leave to appeal", "CR": "Civil revision", "CSA": "Second appeal",
    "ARBAP": "Arbitration application", "ITA": "Income tax appeal",
}
# Short names for the acts lawyers abbreviate anyway.
_ACT_SHORT = {
    "Indian Penal Code, 1860": "IPC", "Code of Criminal Procedure, 1973": "CrPC",
    "Code of Civil Procedure, 1908": "CPC", "Bharatiya Nagarik Suraksha Sanhita, 2023": "BNSS",
    "Bharatiya Nyaya Sanhita, 2023": "BNS", "Bharatiya Sakshya Adhiniyam, 2023": "BSA",
    "Narcotic Drugs and Psychotropic Substances Act, 1985": "NDPS Act",
    "Negotiable Instruments Act, 1881": "NI Act", "Constitution of India": "Constitution",
    "Protection of Children from Sexual Offences Act, 2012": "POCSO Act",
    "Scheduled Castes and Scheduled Tribes (Prevention of Atrocities) Act, 1989": "SC/ST Act",
    "Prevention of Corruption Act, 1988": "PC Act", "Information Technology Act, 2000": "IT Act",
    "Motor Vehicles Act, 1988": "MV Act",
}
# Acts that carry the case into court rather than say what it is about.
_PROCEDURAL = {"Code of Criminal Procedure, 1973", "Bharatiya Nagarik Suraksha Sanhita, 2023",
               "Code of Civil Procedure, 1908", "Constitution of India", "Limitation Act, 1963",
               "General Clauses Act, 1897", "Indian Evidence Act, 1872", "Bharatiya Sakshya Adhiniyam, 2023"}


def _is_procedural(act: str) -> bool:
    return act in _PROCEDURAL or bool(re.search(r"\bProcedure\b", act))


def headline(case_type: str | None, acts: list[dict] | None, outcome_label: str | None,
             disposal: str | None = None) -> str | None:
    """One line to scan a result by: "Bail application · NDPS Act s. 8, 21 · Bail granted"."""
    parts = []
    if case_type:
        # Supreme Court types are already words ("CRIMINAL APPEAL"): just tone down the capitals
        parts.append(CASE_KINDS.get(case_type.upper(), case_type.capitalize() if " " in case_type else case_type))
    # What the case is about (IPC, NDPS Act), not how it reached court (CrPC s. 439), when both are cited.
    acts = acts or []
    acts = [a for a in acts if not _is_procedural(a["act"])][:2] or acts[:1]
    for a in acts:
        name = _ACT_SHORT.get(a["act"], re.sub(r",?\s*\d{4}$", "", a["act"]))
        secs = a.get("sections") or []
        prefix = "art." if a["act"] == "Constitution of India" else "s."
        parts.append(f"{name} {prefix} {', '.join(secs[:4])}{'…' if len(secs) > 4 else ''}" if secs else name)
    result = outcome_label or (disposal.strip().capitalize() if disposal else None)
    if result:
        parts.append(result)
    return " · ".join(parts) if len(parts) > 1 else None


# --------------------------------------------------------------------------- Hindi orders

# Hindi orders (mostly bail) end with the operative words; most specific first.
_HINDI_OUTCOMES: list[tuple[str, re.Pattern]] = [(label, re.compile(rx)) for label, rx in [
    ("Withdrawn", r"वापि?स\s+(?:लि|ले)"),
    ("Bail granted", r"जमानत\s+पर\s+(?:तुरंत\s+|तुरन्त\s+)?(?:रिहा|मुक्त)|जमानत[^।]{0,80}स्वीकार"),
    ("Bail refused", r"जमानत[^।]{0,80}(?:खारिज|अस्वीकार|निरस्त)"),
    ("Sentence suspended", r"सजा[^।]{0,80}(?:स्थगित|निलंबित)"),
    ("Allowed", r"स्वीकार\s+(?:किया|किये|की)"),
    ("Dismissed", r"खारिज|अस्वीकार\s+(?:किया|किये|की)"),
    ("Disposed of", r"निस्तारित|निस्तारण\s+किया"),
]]


def _hindi_sentences(text: str) -> list[str]:
    flat = re.sub(r"\s+", " ", _flatten(text))
    return [re.sub(r"^\d+[.)]\s*", "", p).strip() + "।" for p in re.split(r"\s*।\s*", flat)
            if len(p.strip()) > 20]


def hindi_outcome(body_text: str) -> str | None:
    """The outcome label for a Hindi order, from its last sentences."""
    for sent in reversed(_hindi_sentences(body_text)[-6:]):
        for label, rx in _HINDI_OUTCOMES:
            if rx.search(sent):
                return label
    return None


def hindi_summary(body_text: str, max_chars: int = 600) -> str | None:
    """The first sentence of a Hindi order (what was sought) and the operative one."""
    sents = _hindi_sentences(body_text)
    if not sents:
        return None
    last = next((s for s in reversed(sents[-6:]) if any(rx.search(s) for _, rx in _HINDI_OUTCOMES)), None)
    parts = [sents[0]] if last in (None, sents[0]) else [sents[0], last]
    return " ".join(p if len(p) <= max_chars // len(parts) else
                    p[:max_chars // len(parts) - 1].rsplit(" ", 1)[0] + "…" for p in parts)


# --------------------------------------------------------------------------- all together


def extract(text: str, supreme: bool = False) -> dict:
    """Every structured field for one judgment's text; `supreme` for a Supreme Court judgment."""
    if supreme:
        return extract_sc(text)
    lang = language(text)
    head = header(text)
    citation = neutral_citation(text)
    out = {
        "extractor_version": EXTRACTOR_VERSION,
        "language": lang,
        "neutral_citation": citation,
        "cases": head["cases"],
        "petitioners": head["petitioners"],
        "respondents": head["respondents"],
        "advocates": head["advocates"],
        "judges": head["judges"],
        "order_date": head["order_date"],
        "acts_cited": [],
        "cases_cited": [],
        "case_refs": [],
        "summary": None,
        "outcome": None,
        "key_reasoning": None,
    }
    if lang == "en":
        b = body(text)
        out["acts_cited"] = acts_cited(b)
        out["cases_cited"] = cases_cited(b, citation)
        out["case_refs"] = case_refs(b, own_refs(head["cases"]), out["cases_cited"])
        out["summary"] = summary(b)
        out["outcome"] = outcome(b)
        out["key_reasoning"] = key_reasoning(b)
    elif lang == "hi":
        b = body(text)
        out["summary"] = hindi_summary(b)
        out["outcome"] = hindi_outcome(b)
    return out


# --------------------------------------------------------------------------- Supreme Court Reports

# Supreme Court PDFs are pages of the Supreme Court Reports: "[2024] 10 S.C.R. 108 : 2024 INSC 735",
# the parties either side of "v.", the case number and date, "[A and B,* JJ.]", the reporter's
# headnote, then the judgment. Older volumes print the page's A-H margin letters into the text.
_SCR_RE = re.compile(r"S\.\s?C\.\s?R\.|SUPRE\S{1,3}E COURT REPORTS|\d{4}\s?INSC\s?\d+")
_SC_V_RE = re.compile(r"^(?:(?P<before>.+?)\s+)?(?:v|vs|versus)\s?\.?$|^[~.]\.?$", re.I)
_SC_MARGIN_RE = re.compile(r"^[A-Ha-h]$")
_SC_MARGIN_PREFIX_RE = re.compile(r"^[A-H]\s+(?=[A-Z\[(])")
_SC_INSC_RE = re.compile(r"\b(\d{4})\s?INSC\s?(\d+)\b")
_SC_DATE_RE = re.compile(r"^(?:(?P<mon1>[A-Za-z]+)\s+(?P<day1>\d{1,2}),\s*(?P<year1>\d{4})|"
                         r"(?P<day2>\d{1,2})\s+(?P<mon2>[A-Za-z]+),?\s+(?P<year2>\d{4}))\.?$")
_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july",
                                       "august", "september", "october", "november", "december"], 1)}
_SC_CASE_RE = re.compile(r"^\(.*\bNos?\.", re.I)
_SC_JUDGES_END_RE = re.compile(r"\bJ\s?J\b|\bJ\.\s*[\])J]|[\])]\s*(?:[A-H])?\s*$")
# Running heads: "[2024] 10 S.C.R. 109", "SWAMI NATH v. NIRMAL SINGH 1003", "S.C.R. SUPREME COURT REPORTS 107"
_SC_RUNNING_HEAD_RE = re.compile(r"^(?:\[\d{4}\]\s*\d+\s*S\.C\.R\.\s*\d+|.*\bv\.\s.*\s\d{1,4}(?:\s+[A-H])?|"
                                 r"(?:\d+\s+)?S\.C\.R\.?\s+SUPREME COURT REPORTS(?:\s+\d+)?|H\s+\d{1,4})$")
_SC_HEADINGS_RE = re.compile(r"^(?:Case Law Cited|List of Acts|List of Keywords|Headnotes†?|Issue for Consideration|"
                             r"Case Arising From|Appearances for Parties|Judgment / Order of the Supreme Court|"
                             r"\* Author|†)$", re.I)
_SC_RESULTS = [(label, re.compile(rx, re.I)) for label, rx in [
    ("Partly allowed", r"\b(?:partly|partially)\s+allowed|\ballowed\s+(?:in part|partly)"),
    ("Remanded", r"\bremanded?\b|\bremitted\b"),
    ("Allowed", r"\ballowed\b"),
    ("Dismissed", r"\bdismissed\b"),
    ("Reference answered", r"\breference\s+answered\b|\banswered\b"),
    ("Disposed of", r"\bdisposed\s+of+\b"),
]]


def _sc_lines(text: str) -> list[str]:
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if ln and not _SC_MARGIN_RE.match(ln):
            out.append(ln)
    return out


def _sc_judges(line: str) -> list[str]:
    inner = re.sub(r"^[\[(]", "", line.strip())
    inner = re.sub(r",?\s*(?:J\s?J|J)\b[^A-Za-z]*(?:[A-H]\s*)?$|[\])J]\s*(?:[A-H]\s*)?$", "", inner.strip())
    inner = re.sub(r",?\s*C\.\s?J\.?I?\.?(?=,|$)", "", inner)  # "K. N. WANCHOO, C.J., R. S. BACHAWAT"
    names = re.split(r",|\s+and\s+", inner.replace("*", ""), flags=re.I)
    return [re.sub(r"\s+", " ", n).strip(" .").upper() for n in names if n.strip(" .")]


def sc_header(text: str) -> dict:
    """Parties, case number, judges and date from a Supreme Court Reports page, and the body after them."""
    lines = _sc_lines(text)
    out = {"cases": [], "petitioners": [], "respondents": [], "judges": [], "order_date": None, "body_start": 0}
    head = [_SC_MARGIN_PREFIX_RE.sub("", ln) for ln in lines[:40]]
    v = next((i for i, ln in enumerate(head) if _SC_V_RE.match(ln)), None)
    if v is None:
        return out
    before, tail = [], _SC_V_RE.match(head[v])["before"]
    for ln in head[:v] + ([tail] if tail else []):
        if _SCR_RE.search(ln) or re.fullmatch(r"\d+", ln):
            before = []
            continue
        before.append(ln)
    out["petitioners"] = [" ".join(before)] if before else []
    after, i = [], v + 1
    while i < len(head):
        ln = head[i]
        if _SC_CASE_RE.match(ln):
            out["cases"].append(ln.strip("()"))
        elif _SC_DATE_RE.match(ln):
            m = _SC_DATE_RE.match(ln)
            mon = _MONTHS.get((m["mon1"] or m["mon2"]).lower())
            if mon:
                out["order_date"] = f"{m['year1'] or m['year2']}-{mon:02d}-{int(m['day1'] or m['day2']):02d}"
        elif ln[:1] in "[(" and not out["judges"]:
            j = ln
            while not _SC_JUDGES_END_RE.search(j) and i + 1 < len(head) and i < v + 12:
                i += 1
                j += " " + head[i]
            out["judges"] = _sc_judges(j)
            out["body_start"] = i + 1
            break
        elif not out["cases"] and out["order_date"] is None:
            after.append(ln)
        i += 1
    out["respondents"] = [" ".join(after)] if after else []
    return out


def _sc_body(lines: list[str], head: dict) -> str:
    """The text without running heads, section headings or lines repeated on every page."""
    counts: dict[str, int] = {}
    for ln in lines:
        counts[ln] = counts.get(ln, 0) + 1
    own = " v. ".join(side[0] for side in (head["petitioners"], head["respondents"]) if side).lower()
    keep = [ln for ln in lines if not _SC_RUNNING_HEAD_RE.match(ln) and not _SC_HEADINGS_RE.match(ln)
            and not (counts[ln] > 2 and " v. " in ln) and ln.lower() != own]
    return "\n".join(keep)


def _sc_between(text: str, start: str, end: str) -> str | None:
    m = re.search(rf"(?m)^{start}\s*$(.*?)^{end}", text, re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else None


def sc_outcome(text: str) -> str | None:
    """From "Result of the Case: Appeals disposed of." or, in older reports, the closing line."""
    m = re.search(r"Result of the Case\s*:\s*(.+)", text)
    tail = m.group(1) if m else re.sub(r"\s+", " ", text[-400:])
    for label, rx in _SC_RESULTS:
        if rx.search(tail):
            return label
    return None


def _is_own_case(cited: dict, head: dict) -> bool:
    """Older volumes print the case's own name in the margin; that is not a citation."""
    if cited["citations"] or not cited["name"] or not head["petitioners"] or not head["respondents"]:
        return False
    name = cited["name"].lower()
    return all(max(re.findall(r"[a-z]+", side[0].lower()) or [""], key=len) in name
               for side in (head["petitioners"], head["respondents"]))


def extract_sc(text: str) -> dict:
    """extract() for a Supreme Court judgment: same keys, read from the report's layout."""
    lines = _sc_lines(text)
    head = sc_header(text)
    b = _sc_body(lines[head["body_start"]:], head)
    insc = _SC_INSC_RE.search(text[:400])
    issue = _sc_between(text, "Issue for Consideration", "Headnotes")
    result = re.search(r"Result of the Case\s*:\s*(.+)", text)
    if issue:
        parts = [issue if len(issue) <= 450 else issue[:449].rsplit(" ", 1)[0] + "…"]
        if result:
            parts.append(result.group(1).strip().rstrip(".") + ".")
        brief = " ".join(parts)
    else:
        brief = summary(b)
    return {
        "extractor_version": EXTRACTOR_VERSION,
        "language": language(text),
        "neutral_citation": f"{insc[1]} INSC {insc[2]}" if insc else None,
        "cases": head["cases"],
        "petitioners": head["petitioners"],
        "respondents": head["respondents"],
        "advocates": {"petitioner": [], "respondent": []},
        "judges": head["judges"],
        "order_date": head["order_date"],
        # "s. 120-B of the Penal Code" can leave a stray "B of the Penal Code" act behind
        "acts_cited": [a for a in acts_cited(b) if not re.match(r"[A-Z] of ", a["act"])],
        "cases_cited": [c for c in cases_cited(b) if not _is_own_case(c, head)],
        "case_refs": [],  # these point at Rajasthan HC case numbers, which an SC judgment doesn't use
        "summary": brief,
        "outcome": sc_outcome(text) or outcome(b),
        "key_reasoning": key_reasoning(b),
    }
