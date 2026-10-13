"""Recognise Indian and global corporate identifiers in free text and search queries."""
from __future__ import annotations

import re

from .normalize import normalize_identifier

CIN_RE = re.compile(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$")
CIN_FIND_RE = re.compile(r"\b[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b")
LLPIN_RE = re.compile(r"^[A-Z]{3}-?\d{4}$")
FCRN_RE = re.compile(r"^F\d{5}$")
LEI_RE = re.compile(r"^[A-Z0-9]{18}\d{2}$")
PAN_RE = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")
GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
DIN_RE = re.compile(r"^\d{7,8}$")
IMO_RE = re.compile(r"^(?:IMO)?(\d{7})$")

CIN_STATES = {
    "AN": "Andaman and Nicobar Islands", "AP": "Andhra Pradesh", "AR": "Arunachal Pradesh", "AS": "Assam",
    "BR": "Bihar", "CH": "Chandigarh", "CT": "Chhattisgarh", "DL": "Delhi", "DN": "Dadra and Nagar Haveli",
    "GA": "Goa", "GJ": "Gujarat", "HP": "Himachal Pradesh", "HR": "Haryana", "JH": "Jharkhand",
    "JK": "Jammu and Kashmir", "KA": "Karnataka", "KL": "Kerala", "LD": "Lakshadweep", "MH": "Maharashtra",
    "ML": "Meghalaya", "MN": "Manipur", "MP": "Madhya Pradesh", "MZ": "Mizoram", "NL": "Nagaland", "OR": "Odisha",
    "PB": "Punjab", "PY": "Puducherry", "RJ": "Rajasthan", "SK": "Sikkim", "TG": "Telangana", "TN": "Tamil Nadu",
    "TR": "Tripura", "UP": "Uttar Pradesh", "UR": "Uttarakhand", "WB": "West Bengal",
}
PAN_HOLDER = {
    "P": "person", "C": "company", "H": "hindu_undivided_family", "F": "firm", "A": "association_of_persons",
    "T": "trust", "B": "body_of_individuals", "L": "local_authority", "J": "artificial_juridical_person",
    "G": "government",
}


def lei_checksum_ok(value: str) -> bool:
    """ISO 17442 / ISO 7064 MOD 97-10: the numeric form of a valid LEI is 1 mod 97."""
    if not LEI_RE.match(value):
        return False
    digits = "".join(str(int(ch, 36)) for ch in value)
    return int(digits) % 97 == 1


def parse_cin(cin: str) -> dict | None:
    cin = normalize_identifier("cin", cin)
    if not CIN_RE.match(cin):
        return None
    return {
        "listing": "listed" if cin[0] == "L" else "unlisted",
        "industry_code": cin[1:6],
        "state_code": cin[6:8],
        "state": CIN_STATES.get(cin[6:8]),
        "year": int(cin[8:12]),
        "ownership_code": cin[12:15],
        "registration_number": cin[15:],
    }


def normalize_din(value) -> str | None:
    """DINs are 8 digits; spreadsheets often store them as floats ('6615175.0') without the leading zero."""
    if value is None:
        return None
    text = re.sub(r"\.0+$", "", str(value).strip())
    if not text.isdigit() or not 1 <= len(text) <= 8 or int(text) == 0:
        return None
    return text.zfill(8)


def classify(value: str) -> list[tuple[str, str]]:
    """Every identifier scheme a query could be, most specific first. Empty for plain names."""
    raw = (value or "").strip()
    compact = normalize_identifier("", raw)
    out: list[tuple[str, str]] = []
    if CIN_RE.match(compact):
        out.append(("cin", compact))
    if LEI_RE.match(compact) and lei_checksum_ok(compact):
        out.append(("lei", compact))
    if GSTIN_RE.match(compact):
        out.append(("gstin", compact))
        out.append(("pan", compact[2:12]))
    if PAN_RE.match(compact):
        out.append(("pan", compact))
    if LLPIN_RE.match(raw.upper()):
        out.append(("llpin", compact))
    if FCRN_RE.match(compact):
        out.append(("fcrn", compact))
    if DIN_RE.match(compact):
        out.append(("din", compact.zfill(8)))
    imo = IMO_RE.match(compact)
    if imo and (compact.startswith("IMO") or not out):
        out.append(("imo", "IMO" + imo.group(1)))  # OpenSanctions stores IMO numbers with the prefix
    return out


def detect_scheme(value: str) -> str:
    """Best scheme for an unlabelled identifier (e.g. OpenSanctions simple CSV); 'id_number' if unknown."""
    compact = normalize_identifier("", value)
    if compact.startswith("IMO") and IMO_RE.match(compact):
        return "imo"
    found = classify(value)
    return found[0][0] if found and found[0][0] != "din" else "id_number"
