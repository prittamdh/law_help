"""Bare acts: the text of each section, and which old section became which new one.

The data is built by scripts/build_bare_acts.py and scripts/build_section_map.py and committed
under law_help/data, so nothing here touches the network or the database.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data"

# The order acts are listed in: the criminal codes old and new side by side, then the rest.
ORDER = ["bns", "ipc", "bnss", "crpc", "bsa", "evidence", "constitution", "cpc", "ni-act",
         "mv-act", "contract", "it-act", "ndps", "pocso", "sc-st-act", "arms-act"]
# Which code replaced which, from 1 July 2024.
REPLACED = {"ipc": "bns", "crpc": "bnss", "evidence": "bsa"}


@lru_cache(maxsize=1)
def acts() -> dict[str, dict]:
    out = {}
    for slug in ORDER:
        path = DATA / "acts" / f"{slug}.json"
        if path.exists():
            act = json.loads(path.read_text())
            act["index"] = {s["number"]: s for s in act["sections"]}
            out[slug] = act
    return out


@lru_cache(maxsize=1)
def _by_name() -> dict[str, str]:
    return {a["act"]: slug for slug, a in acts().items()}


def slug_for(act_name: str) -> str | None:
    """'Indian Penal Code, 1860' (as law_help.extract.canonical_act names it) -> 'ipc'."""
    return _by_name().get(act_name)


def base_number(ref: str) -> str:
    """'103(1)' -> '103', '65(2)' -> '65', 'Order VII Rule 11' stays as it is."""
    m = re.match(r"(\d+[A-Z]*)(?:\(|$)", ref)
    return m[1] if m else ref


def section_key(ref: str) -> str:
    """How a judgment's acts_cited writes a CPC order: 'Order 7 Rule 11' -> 'Order VII Rule 11'."""
    m = re.match(r"Order\s+(\d+)\s+Rule\s+(\S+)$", ref, re.I)
    return f"Order {_roman(int(m[1]))} Rule {m[2]}" if m else ref


def _roman(n: int) -> str:
    out = ""
    for value, letters in ((50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= value:
            out += letters
            n -= value
    return out


def _arabic(roman: str) -> int:
    values = {"I": 1, "V": 5, "X": 10, "L": 50}
    total = 0
    for a, b in zip(roman, roman[1:] + " "):
        total += -values[a] if values.get(b, 0) > values[a] else values[a]
    return total


def get_section(slug: str, number: str) -> dict | None:
    act = acts().get(slug)
    if not act:
        return None
    return act["index"].get(number) or act["index"].get(section_key(number)) \
        or act["index"].get(base_number(number))


@lru_cache(maxsize=1)
def _map() -> list[dict]:
    path = DATA / "section_map.json"
    return json.loads(path.read_text()) if path.exists() else []


def equivalents(slug: str, number: str) -> list[dict]:
    """The same provision in the other code: IPC 302 -> [{"act": "bns", "ref": "103", ...}].

    "ref" is as precise as the source gives it (BNS "318(4)" for IPC 420); "number" is the
    section to link to. "new": None marks an old section the new code dropped (IPC 377).
    """
    out, seen = [], set()
    for row in _map():
        if row["old_act"] == slug and row["old"] in (number, base_number(number)):
            other, ref = row["new_act"], row["new"]
        elif row["new_act"] == slug and row["new"] is not None and base_number(row["new"]) == base_number(number) \
                and (row["new"] == number or "(" not in number or "(" not in row["new"]):
            other, ref = row["old_act"], row["old"]
        else:
            continue
        if (other, ref) in seen:
            continue
        seen.add((other, ref))
        act = acts().get(other, {})
        target = get_section(other, ref) if ref else None
        out.append({
            "act": other, "short": act.get("short", other), "ref": ref,
            "number": target["number"] if target else None,
            "title": target["title"] if target else None,
            "direction": "new" if REPLACED.get(slug) == other else "old",
            # IPC -> BNS is NCRB's published table; the others are matched by wording.
            "source": row["source"],
        })
    return sorted(out, key=lambda e: (e["ref"] is None, _num(e["ref"] or "")))


def _num(ref: str) -> tuple:
    m = re.match(r"(\d+)([A-Z]*)(.*)", ref)
    return (int(m[1]), m[2], m[3]) if m else (0, ref, "")


def cited_forms(slug: str, number: str) -> list[str]:
    """Every way acts_cited may write this section: '103' plus '103(1)', '103(2)' from its text."""
    sec = get_section(slug, number)
    forms = [number]
    if sec and sec["number"] != number:
        forms.append(sec["number"])
    if m := re.match(r"Order ([IVXL]+) Rule (\S+)$", number):
        forms.append(f"Order {_arabic(m[1])} Rule {m[2]}")  # "Order 7 Rule 11" is common too
    if sec and "(" not in number and not number.startswith("Order"):
        subs = re.findall(r"(?:^|\n)\s*\[?\((\d{1,2}[A-Z]?)\)", sec["text"])
        forms += [f"{sec['number']}({s})" for s in dict.fromkeys(subs)]
    return list(dict.fromkeys(forms))


def search_forms(act_name: str, section: str, include_equivalents: bool = True) -> tuple[list[dict], list[dict]]:
    """acts_cited fragments that match a section, and the equivalents they add.

    ("Indian Penal Code, 1860", "302") -> ([{"act": IPC, "sections": ["302"]},
    {"act": BNS, "sections": ["103"]}, {"act": BNS, "sections": ["103(1)"]}, ...], [BNS 103]).
    """
    slug = slug_for(act_name)
    if not slug:
        return [{"act": act_name, "sections": [section]}], []
    frags = [{"act": act_name, "sections": [f]} for f in cited_forms(slug, section_key(section))]
    added = []
    if include_equivalents:
        for eq in equivalents(slug, section_key(section)):
            if not eq["ref"] or eq["act"] not in acts():
                continue
            other = acts()[eq["act"]]["act"]
            # "318(4)", and plain "318" since many orders cite a section without its sub-section.
            refs = [eq["ref"], base_number(eq["ref"])] if "(" in eq["ref"] else cited_forms(eq["act"], eq["ref"])
            frags += [{"act": other, "sections": [r]} for r in refs]
            added.append(eq)
    unique = {json.dumps(f, sort_keys=True): f for f in frags}
    return list(unique.values()), added
