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

EXTRACTOR_VERSION = 1

# --------------------------------------------------------------------------- PDF text


# eCourts stamps every page of some PDFs; scanned ones carry nothing else.
_WATERMARK_RE = re.compile(r"^\(Downloaded on .*\)$")


def pdf_text(data: bytes) -> str | None:
    """Extract text in pypdf's layout mode, then tidy whitespace line by line.

    The default mode splits words at kerning gaps ("Dist rict"), which breaks names.
    """
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = [page.extract_text(extraction_mode="layout") or "" for page in reader.pages]
    lines = []
    for line in "\n".join(pages).replace("\x00", "").splitlines():
        line = re.sub(r"[ \t ]+", " ", line).strip()
        if line and not _WATERMARK_RE.match(line):
            lines.append(line)
    return "\n".join(lines) or None


# Many Hindi orders are typed in the legacy Kruti Dev font, so their text layer is
# Latin gibberish ("izkFkhZx.k@vfHk;qDrx.k"). The header is still English, so parties,
# advocates and judges parse, but acts, citations and the summary are skipped.
_KRUTI_RE = re.compile(r"\b(?:gS|dk|ds|esa|fd;k|vkSj|dh|;g|rFkk|fd)\b")


def language(text: str) -> str:
    """"en", "hi", "hi-krutidev", or "no-text" for a scan with no text layer (needs OCR)."""
    if len(re.findall(r"[A-Za-z\u0900-\u097F]", text)) < 200:
        return "no-text"
    hits = len(_KRUTI_RE.findall(text))
    if hits >= 5 and hits / max(1, len(text.split())) > 0.02:
        return "hi-krutidev"
    if re.search(r"[ऀ-ॿ]{20,}", text):
        return "hi"
    return "en"


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
    (r"I\.?\s?P\.?\s?C\.?|Indian Penal Code(?:,? 1860)?", "Indian Penal Code, 1860"),
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
            order = re.search(r"[IVXL\d]+", m.group("kind")[5:]).group()
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


# --------------------------------------------------------------------------- all together


def extract(text: str) -> dict:
    """Every structured field for one judgment's text."""
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
        "summary": None,
    }
    if lang == "en":
        b = body(text)
        out["acts_cited"] = acts_cited(b)
        out["cases_cited"] = cases_cited(b, citation)
        out["summary"] = summary(b)
    return out
