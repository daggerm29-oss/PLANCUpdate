"""Deterministic normalisation for names, addresses and identifiers.

Names are case/diacritic folded, Cyrillic is transliterated (so GLEIF's Russian
legal names meet OpenSanctions' Latin names), punctuation removed, and legal-form
words stripped to a "core" name used for matching. Originals are never altered.
"""
from __future__ import annotations

import re
import unicodedata

_CYRILLIC = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya", "і": "i", "ї": "yi", "є": "ye", "ґ": "g",
}

_LEGAL_PHRASES = [
    "public joint stock company", "open joint stock company", "closed joint stock company", "joint stock company",
    "non public joint stock company", "limited liability company", "limited liability partnership",
    "public limited company", "private limited company", "limited partnership", "societe anonyme",
    "aktiengesellschaft", "gesellschaft mit beschrankter haftung",
    # transliterated Russian forms (after _CYRILLIC mapping)
    "publichnoe aktsionernoe obshchestvo", "nepublichnoe aktsionernoe obshchestvo", "otkrytoe aktsionernoe obshchestvo",
    "zakrytoe aktsionernoe obshchestvo", "aktsionernoe obshchestvo", "obshchestvo s ogranichennoy otvetstvennostyu",
    "kommercheskiy bank", "aktsionernyy kommercheskiy bank",
]
_LEGAL_TOKENS = {
    "ltd", "limited", "llc", "plc", "inc", "incorporated", "corp", "corporation", "co", "company", "gmbh", "ag", "sa",
    "spa", "srl", "sarl", "bv", "nv", "oy", "ab", "asa", "kg", "kft", "sro", "llp", "lp", "pte", "pty", "bhd", "sdn",
    "pjsc", "ojsc", "cjsc", "jsc", "pao", "oao", "zao", "ooo", "ao", "nao", "the", "and",
    # Indian forms: "Pvt Ltd", "Private Limited", "(OPC)", "LLP"
    "pvt", "private", "opc",
}
_INDIAN_PREFIX_RE = re.compile(r"^(?:m s|messrs)\s+")  # "M/s. Foo Ltd" -> "foo"
_PHRASE_RE = re.compile(r"\b(?:" + "|".join(sorted(map(re.escape, _LEGAL_PHRASES), key=len, reverse=True)) + r")\b")
_NON_WORD = re.compile(r"[^\w\s]|_", re.UNICODE)
_SPACES = re.compile(r"\s+")

_ADDRESS_TOKENS = {
    "street": "st", "str": "st", "road": "rd", "avenue": "ave", "av": "ave", "boulevard": "blvd", "suite": "ste",
    "floor": "fl", "building": "bldg", "number": "no", "ulitsa": "ul", "ulica": "ul", "prospekt": "pr", "dom": "d",
}


def fold(text: str) -> str:
    lowered = "".join(_CYRILLIC.get(ch, ch) for ch in text.lower())
    decomposed = unicodedata.normalize("NFKD", lowered)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _clean(text: str) -> str:
    return _SPACES.sub(" ", _NON_WORD.sub(" ", fold(text).replace("&", " and "))).strip()


def normalize_name(name: str | None) -> str:
    """Core matching form: folded, punctuation-free, legal forms removed."""
    if not name:
        return ""
    cleaned = _INDIAN_PREFIX_RE.sub("", _clean(name))
    without_phrases = _SPACES.sub(" ", _PHRASE_RE.sub(" ", cleaned)).strip()
    tokens = [t for t in without_phrases.split() if t not in _LEGAL_TOKENS]
    return " ".join(tokens) or cleaned


def normalize_address(address: str | None) -> str:
    if not address:
        return ""
    tokens = [_ADDRESS_TOKENS.get(t, t) for t in _clean(address).split()]
    return " ".join(tokens)


def normalize_identifier(scheme: str, value: str | None) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if scheme == "din":
        text = re.sub(r"\.0+$", "", text)  # spreadsheets store DINs as floats
    cleaned = re.sub(r"[\s\-./]", "", text).upper()
    if scheme == "bic" and len(cleaned) == 11 and cleaned.endswith("XXX"):
        cleaned = cleaned[:8]
    if scheme == "din" and cleaned.isdigit():
        cleaned = cleaned.zfill(8)
    return cleaned


def search_tokens(text: str, limit: int = 8) -> list[str]:
    """Tokens safe to embed in an FTS5 query (quoted by the caller)."""
    out: list[str] = []
    for token in _clean(text).split():
        if len(token) >= 2 and token not in out:
            out.append(token)
    return out[:limit]
