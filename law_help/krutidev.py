"""Convert text typed in the legacy Kruti Dev 010 font to Unicode Hindi.

Many Hindi orders were typed in Kruti Dev, a font that draws Devanagari over Latin key
codes, so their PDF text layer reads "izkFkhZ dh vksj ls" instead of "प्रार्थी की ओर से".
The case header of such orders is still in English, and English phrases appear in
brackets inside the Hindi ("(Balance Sheet)"), so `convert_mixed` converts only the
Kruti Dev lines and keeps English lines and bracketed English as they are.

Kruti Dev stores glyphs in visual order: the ि sign comes before its consonant ("f")
and the reph (र् above a letter, "Z") after its syllable, so both are moved into
Unicode's logical order after the glyph mapping.
"""

import re

_PRE_I = "\x01"   # placeholder for "f", the ि drawn before its consonant
_REPH = "\x02"    # placeholder for "Z", the र् drawn after its syllable

# Kruti Dev glyph sequences and their Unicode text, matched longest first.
_GLYPHS = {
    # independent vowels
    "vkW": "ऑ", "v‚": "ऑ", "vks": "ओ", "vkS": "औ", "vk": "आ", "v": "अ",
    "b±": "ईं", "bZ": "ई", "Ã": "ई", "b": "इ", "m": "उ", "Å": "ऊ", ",s": "ऐ", ",": "ए", "_": "ऋ",
    # consonants: a Kruti half form plus "k" (the ा stroke) makes the full letter
    "d": "क", "D": "क्", "[k": "ख", "[": "ख्", "x": "ग", "Xk": "ग", "X": "ग्", "?k": "घ", "?": "घ्",
    "Ä": "घ", "³": "ङ", "p": "च", "Pk": "च", "P": "च्", "N": "छ", "t": "ज", "Tk": "ज", "T": "ज्",
    ">": "झ", "÷": "झ्", "Ö": "झ्", "¥": "ञ", "V": "ट", "B": "ठ", "M": "ड", "<": "ढ", ".k": "ण",
    ".": "ण्", "r": "त", "Rk": "त", "R": "त्", "Fk": "थ", "F": "थ्", "n": "द", "/k": "ध", "/": "ध्",
    "èk": "ध", "è": "ध्", "Ë": "ध्", "u": "न", "Uk": "न", "U": "न्", "i": "प", "Ik": "प", "I": "प्",
    "Q": "फ", "¶": "फ्", "c": "ब", "Ck": "ब", "C": "ब्", "Hk": "भ", "H": "भ्", "Ò": "भ", "e": "म",
    "Ek": "म", "E": "म्", ";": "य", "¸": "य्", "j": "र", "y": "ल", "Yk": "ल", "Y": "ल्", "G": "ळ",
    "o": "व", "Ok": "व", "O": "व्", "'k": "श", "'": "श्", "Ük": "श", "Ü": "श्", "\"k": "ष", "\"": "ष्",
    "’k": "ष", "’": "ष्", "”k": "श", "”": "श्", "l": "स", "Lk": "स", "L": "स्", "g": "ह",
    # conjuncts
    "{k": "क्ष", "{": "क्ष्", "=": "त्र", "«": "त्र्", "K": "ज्ञ", "J": "श्र", "}": "द्व", "|": "द्य",
    "Ùk": "त्त", "Ù": "त्त्", "ä": "क्त", "–": "दृ", "—": "कृ", "Ñ": "कृ", "é": "न्न", "™": "न्न्",
    "à": "ह्न", "á": "ह्य", "â": "हृ", "ã": "ह्म", "º": "ह्", "í": "द्द", "Ì": "द्द", "ô": "क्क",
    "Ø": "क्र", "Ý": "फ्र", "æ": "द्र", "ç": "प्र", "Á": "प्र", "#": "रु", ":": "रू", ")": "द्ध",
    "ê": "ट्ट", "Í": "ट्ट", "ë": "ट्ठ", "Î": "ट्ठ", "ì": "ड्ड", "Ï": "ड्ड", "ï": "ड्ढ", "Ô": "ड्ढ",
    "ª": "्र", "z": "्र", "î": "्य", "Ó": "्य",
    # vowel signs and marks
    "ks": "ो", "kS": "ौ", "kW": "ॉ", "‚": "ॉ", "k": "ा", "h": "ी", "È": "ीं", "q": "ु", "w": "ू",
    "`": "ृ", "s": "े", "S": "ै", "a": "ं", "¡": "ँ", "%": "ः", "W": "ॅ", "~": "्", "+": "़",
    "f": _PRE_I, "Z": _REPH, "±": _REPH + "ं",
    # punctuation and digits
    "AA": "॥", "A": "।", "]": ",", "^": "‘", "*": "’", "Þ": "“", "ß": "”", "¼": "(", "½": ")",
    "¿": "{", "À": "}", "&": "-", "-": ".", "@": "/", "(": ";", "¾": "=", "\\": "?", "Œ": "॰",
    "ñ": "॰", "·": "ऽ", "å": "०", "ƒ": "१", "„": "२", "…": "३", "†": "४", "‡": "५", "ˆ": "६",
    "‰": "७", "Š": "८", "‹": "९",
}
# Runs of underscores are rules drawn across the page, not ऋ.
_GLYPH_RE = re.compile("_{2,}|" + "|".join(re.escape(k) for k in sorted(_GLYPHS, key=len, reverse=True)))

_CONSONANT = "[\u0915-\u0939\u0958-\u095f]"
_MATRAS = "ािीुूृॄेैोौॉॅंँः़"
# A consonant cluster: letters joined by halants, with any nukta or subscript ्र/्य.
_CLUSTER = rf"{_CONSONANT}़?(?:्{_CONSONANT}़?)*"
_PRE_I_RE = re.compile(rf"{_PRE_I}({_CLUSTER})")
_REPH_RE = re.compile(rf"({_CLUSTER}[{_MATRAS}]*){_REPH}")


def to_unicode(text: str) -> str:
    """Kruti Dev 010 text to Unicode Devanagari (the whole string is taken as Kruti Dev)."""
    # PDF extraction splits words before vowel signs ("vko snu", "dk s"): nothing in
    # Devanagari starts with a vowel sign, so join them back.
    text = re.sub(r"(?<=\S) (?=[sSaZkhqw`+¡%‚±W])", "", text)
    out = _GLYPH_RE.sub(lambda m: _GLYPHS.get(m.group(), m.group()), text)
    out = _PRE_I_RE.sub(lambda m: m.group(1) + "ि", out)
    out = _REPH_RE.sub(lambda m: "र्" + m.group(1), out)
    out = out.replace(_PRE_I, "ि").replace(_REPH, "र्")
    # a sign split around a moved reph ("rdkZsa" → तर्का + े) recombines
    # the PDF sometimes places a nukta or ं after the vowel sign it sits on ("Mh+", "r a k")
    out = re.sub("([ािीुूृेैोौ]+)़", "़\\1", out)
    out = re.sub("([ंँ])([ािीुूृेैोौ])", "\\2\\1", out)
    out = out.replace("ाे", "ो").replace("ाै", "ौ").replace("अा", "आ").replace("आे", "ओ").replace("आै", "औ")
    return out


# --------------------------------------------------------------------------- mixed text

# Words that are Kruti Dev for the commonest Hindi words (है, का, के, की, में, किया, ...).
_KRUTI_WORDS = frozenset(
    "gS gSa dk ds dh esa fd;k vkSj ;g rFkk fd ls dks us ij rks gh Hkh ugha tks og bl ,oa x;k "
    "x;s x;h tk gq, gqvk djrs fn;k ;k vFkok mDr izdj.k tekur U;k;ky; vkns'k".split())
# English words common in judgments; a line with more of these than Kruti Dev words stays English.
_ENGLISH_WORDS = frozenset(
    "the of and to in is for on by with that has was been be as at an or not this which are from "
    "said court state petitioner petitioners respondent respondents accused bail application "
    "versus order section act mr ms mrs shri smt sri high justice hon'ble through pp for dated "
    "judgment appeal writ petition learned counsel no date jail years aged about".split())
_KRUTI_SIGN_RE = re.compile(r"[;@\]\[{}~^¼½`\\|=%\x80-\xff’]|[a-z][A-Z]|[a-z]A$")


def _word_kind(word: str) -> str | None:
    """"k" for a Kruti Dev word, "e" for an English one, None when it could be either."""
    if any(c.isdigit() for c in word):
        return None                                   # dates, case numbers, citations
    bare = word.strip(".,:()'\"-")
    if not bare:
        return None
    if bare in _KRUTI_WORDS or _KRUTI_SIGN_RE.search(bare):
        return "k"
    if bare.lower() in _ENGLISH_WORDS or (len(bare) >= 2 and bare.isupper() and bare.isalpha()):
        return "e"
    return None


def _line_kind(line: str) -> str | None:
    kinds = [_word_kind(w) for w in line.split()]
    k, e = kinds.count("k"), kinds.count("e")
    if k > e:
        return "k"
    if e > k or (e and e == k):
        return "e"
    return None


# English inside a Hindi sentence is typed in ordinary brackets, since Kruti Dev draws
# its own brackets as ¼ ½ ("(" and ")" are ; and द्ध there).
_ADVOCATE_RE = re.compile(r"^(For\b[^:]*:\s*)(.+)$")
_BRACKETED_RE = re.compile(r"\(([^()\n]+(?:\n[^()\n]+)?)\)")


def _is_english_phrase(s: str) -> bool:
    words = re.findall(r"\S+", s)
    return bool(words) and all(_word_kind(w) != "k" and re.fullmatch(r"[A-Za-z.,'&/-]+", w.strip(",."))
                               for w in words if not w.isdigit()) \
        and any(len(w) >= 3 and re.search(r"[aeiouAEIOU]", w) for w in words)


def _convert_kruti_part(text: str) -> str:
    parts, last = [], 0
    for m in _BRACKETED_RE.finditer(text):
        if _is_english_phrase(m.group(1)):
            parts += [to_unicode(text[last:m.start()]), m.group()]
            last = m.end()
    parts.append(to_unicode(text[last:]))
    return "".join(parts)


def convert_mixed(text: str) -> str:
    """Convert the Kruti Dev lines of a judgment, keeping its English lines as they are.

    A line with no telling words (a lone "blds") goes with the line before it.
    """
    lines = text.split("\n")
    kinds, prev = [], "e"
    for ln in lines:
        prev = "e" if _ADVOCATE_RE.match(ln) else _line_kind(ln) or prev
        kinds.append(prev)
    out, block = [], []
    for ln, kind in zip(lines, kinds):
        if kind == "k":
            block.append(ln)
            continue
        if block:
            out.append(_convert_kruti_part("\n".join(block)))
            block = []
        # "For Petitioner(s) : Jh vfuy dqekj tSu": an advocate's name typed in Kruti Dev
        m = _ADVOCATE_RE.match(ln)
        if m and _line_kind(m.group(2)) == "k":
            ln = m.group(1) + to_unicode(m.group(2))
        out.append(ln)
    if block:
        out.append(_convert_kruti_part("\n".join(block)))
    return "\n".join(out)
