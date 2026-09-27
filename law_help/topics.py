"""Practice-area topics (bail, cheque bounce, NDPS...) as rules over fields we already extract.

A judgment belongs to a topic when any one of the topic's rules matches it:

* `cites`: it cites one of these sections (acts_cited), or any section of an act listed with
  no sections. Sections match their sub-sections as judgments cite them (376 finds "376(2)").
  Old and new codes are both listed by hand, so each topic says exactly what it covers.
* `case_types`: its eCourts case type is one of these codes (CRLMB is a bail application).
* `case_type_prefixes`: its case type starts with this (Supreme Court types are words,
  "WRIT PETITION (CIVIL)").
* `keywords`: the phrase is in the title or the opening lines (the Supreme Court's headnote),
  which are weights A and B of `search`. Not the full text: "bail" is somewhere in most
  criminal orders.

Every rule is an indexed condition (GIN on acts_cited and search, btree on case_type), so a
topic is one SQL condition that /judgments can filter by. Topics overlap: a bail order under
the NDPS Act is in both. Rules only, no model.
"""

import re

from psycopg.types.json import Jsonb

from . import bareacts

IPC = "Indian Penal Code, 1860"
BNS = "Bharatiya Nyaya Sanhita, 2023"
CRPC = "Code of Criminal Procedure, 1973"
BNSS = "Bharatiya Nagarik Suraksha Sanhita, 2023"

TOPICS: list[dict] = [
    {
        "slug": "bail", "name": "Bail",
        "blurb": "Regular and anticipatory bail, cancellation of bail, and suspension of sentence pending appeal.",
        "cites": [(CRPC, ["436", "436A", "437", "438", "439", "389"]),
                  (BNSS, ["478", "479", "480", "482", "483", "430"])],
        "case_types": ["CRLMB", "CRLBC", "SOSA", "SOSR"],
        "keywords": ["bail"],
    },
    {
        "slug": "cheque-bounce", "name": "Cheque bounce",
        "blurb": "Dishonour of cheques under section 138 of the Negotiable Instruments Act.",
        "cites": [("Negotiable Instruments Act, 1881", ["138", "139", "141", "142", "143A", "147", "148"])],
        "keywords": ["dishonour of cheque", "cheque dishonour", "dishonoured cheque", "cheque bounce"],
    },
    {
        "slug": "ndps", "name": "NDPS",
        "blurb": "Offences under the Narcotic Drugs and Psychotropic Substances Act: bail, trials and appeals.",
        "cites": [("Narcotic Drugs and Psychotropic Substances Act, 1985", [])],
        "keywords": ["NDPS", "narcotic drugs"],
    },
    {
        "slug": "motor-accident", "name": "Motor accident claims",
        "blurb": "Compensation for road accidents before the Motor Accident Claims Tribunal (MACT) and appeals.",
        "cites": [("Motor Vehicles Act, 1988", ["140", "163A", "164", "166", "168", "173"])],
        "keywords": ["motor accident", "accident claim", "claims tribunal", "MACT"],
    },
    {
        "slug": "service", "name": "Service matters",
        "blurb": "Government employment: appointment, pay, seniority, promotion, discipline, pension.",
        "cites": [("Rajasthan Civil Services (Pension) Rules, 1996", []),
                  ("Rajasthan Civil Services (Classification, Control and Appeal) Rules, 1958", []),
                  ("Rajasthan Service Rules, 1951", []),
                  ("Rajasthan Civil Services (Revised Pay) Rules, 2008", []),
                  ("Rajasthan Civil Services (Revised Pay) Rules, 2017", [])],
        "keywords": ["pension", "seniority", "promotion", "regularisation", "regularization", "pay scale",
                     "compassionate appointment", "disciplinary proceedings", "departmental enquiry",
                     "civil services"],
    },
    {
        "slug": "land-revenue", "name": "Land revenue and tenancy",
        "blurb": "Agricultural land, khatedari rights, mutation and revenue court orders.",
        "cites": [("Rajasthan Land Revenue Act, 1956", []), ("Rajasthan Tenancy Act, 1955", [])],
        "keywords": ["khatedari", "board of revenue", "revenue appellate authority", "tenancy act"],
    },
    {
        "slug": "matrimonial", "name": "Matrimonial and maintenance",
        "blurb": "Maintenance for wives, children and parents, divorce, and domestic violence.",
        "cites": [(CRPC, ["125", "127", "128"]), (BNSS, ["144", "146", "147"]),
                  ("Hindu Marriage Act, 1955", []), ("Protection of Women from Domestic Violence Act, 2005", [])],
        "keywords": ["maintenance allowance", "interim maintenance", "divorce", "conjugal rights",
                     "family court", "domestic violence"],
    },
    {
        "slug": "arbitration", "name": "Arbitration",
        "blurb": "Appointing arbitrators, setting aside and enforcing awards.",
        "cites": [("Arbitration and Conciliation Act, 1996", [])],
        "case_types": ["ARBAP"],
        "keywords": ["arbitration", "arbitral"],
    },
    {
        "slug": "writs", "name": "Constitutional writs",
        "blurb": "Writ petitions under Articles 226 and 227 in the High Court and Article 32 in the Supreme Court.",
        "cites": [("Constitution of India", ["226", "227", "32"])],
        "case_types": ["CW", "CRLW", "HC"],
        "case_type_prefixes": ["WRIT PETITION"],
        "keywords": ["habeas corpus"],
    },
    {
        "slug": "dowry-cruelty", "name": "Dowry and cruelty",
        "blurb": "Cruelty to a wife, dowry demands and dowry deaths.",
        "cites": [(IPC, ["498A", "304B"]), (BNS, ["80", "85", "86"]), ("Dowry Prohibition Act, 1961", [])],
        "keywords": ["dowry"],
    },
    {
        "slug": "murder", "name": "Murder",
        "blurb": "Murder trials and appeals under IPC section 302 and BNS section 103.",
        "cites": [(IPC, ["302"]), (BNS, ["103"])],
        "keywords": ["murder"],
    },
    {
        "slug": "rape-pocso", "name": "Rape and POCSO",
        "blurb": "Rape and sexual offences against children.",
        "cites": [(IPC, ["376", "376A", "376AB", "376B", "376C", "376D", "376DA", "376DB", "376E"]),
                  (BNS, ["64", "65", "66", "67", "68", "69", "70"]),
                  ("Protection of Children from Sexual Offences Act, 2012", [])],
        "keywords": ["rape", "POCSO"],
    },
    {
        "slug": "land-acquisition", "name": "Land acquisition",
        "blurb": "Compulsory acquisition of land by the State and compensation for it.",
        "cites": [("Land Acquisition Act, 1894", []), ("Rajasthan Land Acquisition Act, 1953", []),
                  ("Right to Fair Compensation and Transparency in Land Acquisition, Rehabilitation "
                   "and Resettlement Act, 2013", [])],
        "keywords": ["land acquisition", "acquisition of land"],
    },
    {
        "slug": "excise", "name": "Excise",
        "blurb": "Liquor offences and licences under the Rajasthan Excise Act.",
        "cites": [("Rajasthan Excise Act, 1950", [])],
        "keywords": ["rajasthan excise", "illicit liquor"],
    },
    {
        "slug": "sc-st", "name": "SC/ST atrocities",
        "blurb": "Offences under the Scheduled Castes and Scheduled Tribes (Prevention of Atrocities) Act.",
        "cites": [("Scheduled Castes and Scheduled Tribes (Prevention of Atrocities) Act, 1989", [])],
        "keywords": ["prevention of atrocities"],
    },
    {
        "slug": "corruption", "name": "Corruption",
        "blurb": "Bribery and other offences under the Prevention of Corruption Act.",
        "cites": [("Prevention of Corruption Act, 1988", [])],
        "keywords": ["prevention of corruption"],
    },
]

BY_SLUG = {t["slug"]: t for t in TOPICS}


def get(slug: str) -> dict | None:
    return BY_SLUG.get(slug)


def cited_fragments(topic: dict) -> list[dict]:
    """acts_cited fragments any one of which puts a judgment in the topic."""
    frags = []
    for act, sections in topic.get("cites", []):
        if not sections:
            frags.append({"act": act})
        for s in sections:
            frags += bareacts.search_forms(act, s, include_equivalents=False)[0]
    return frags


def keyword_query(keywords: list[str]) -> str | None:
    """to_tsquery text for the phrases in the title or opening lines:
    ["dishonour of cheque", "NDPS"] -> 'dishonour:AB <-> of:AB <-> cheque:AB | ndps:AB'."""
    phrases = []
    for k in keywords:
        words = re.findall(r"[a-z0-9]+", k.lower())
        if words:
            phrases.append(" <-> ".join(f"{w}:AB" for w in words))
    return " | ".join(f"({p})" for p in phrases) or None


def topic_sql(topic: dict, prefix: str = "topic") -> tuple[str, dict]:
    """One SQL condition, and its parameters, that matches the topic's judgments."""
    parts, params = [], {}
    for i, frag in enumerate(cited_fragments(topic)):
        parts.append(f"acts_cited @> %({prefix}_a{i})s")
        params[f"{prefix}_a{i}"] = Jsonb([frag])
    if topic.get("case_types"):
        parts.append(f"case_type = ANY(%({prefix}_ct)s)")
        params[f"{prefix}_ct"] = topic["case_types"]
    if topic.get("case_type_prefixes"):
        parts.append(f"case_type LIKE ANY(%({prefix}_ctp)s)")
        params[f"{prefix}_ctp"] = [p + "%" for p in topic["case_type_prefixes"]]
    if kw := keyword_query(topic.get("keywords", [])):
        parts.append(f"search @@ to_tsquery('english', %({prefix}_kw)s)")
        params[f"{prefix}_kw"] = kw
    return "(" + " OR ".join(parts) + ")", params


def describe(topic: dict) -> list[str]:
    """The rules in words, for the topic page: ["IPC s. 302", "BNS s. 103", "case type CRLMB", ...]."""
    from .extract import _ACT_SHORT
    out = []
    for act, sections in topic.get("cites", []):
        name = _ACT_SHORT.get(act, act)
        prefix = "art." if act == "Constitution of India" else "s."
        out.append(f"{name} {prefix} {', '.join(sections)}" if sections else f"{name} (any section)")
    if topic.get("case_types"):
        out.append("case type " + ", ".join(topic["case_types"]))
    if topic.get("case_type_prefixes"):
        out.append("Supreme Court " + ", ".join(p.lower() + "s" for p in topic["case_type_prefixes"]))
    if topic.get("keywords"):
        out.append("in the title or opening lines: " + ", ".join(f"“{k}”" for k in topic["keywords"]))
    return out
