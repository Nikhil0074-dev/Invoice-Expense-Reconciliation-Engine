"""Money, date and currency parsing. Never uses float."""
import re
from datetime import date
from decimal import Decimal, InvalidOperation

CODES = {"INR", "USD", "EUR", "GBP", "AUD", "CAD", "SGD", "AED", "JPY"}
SYMBOLS = {"₹": "INR", "€": "EUR", "£": "GBP", "Rs.": "INR", "Rs": "INR"}  # "$" is ambiguous: never mapped
MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def parse_amount(text):
    """Return Decimal or None. Handles 1,234.56 / 1.234,56 / 1,23,456.00 / 1234,56."""
    if text is None:
        return None
    s = re.sub(r"[^\d,.\-()\s]", "", str(text)).strip()
    neg = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    s = re.sub(r"[\s()\-]", "", s)
    if not s or not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        grp = "." if dec == "," else ","
        s = s.replace(grp, "").replace(dec, ".")
    elif "," in s:
        if re.fullmatch(r"\d{1,3}(,\d{2})*,\d{3}|\d{1,3}(,\d{3})+", s):
            s = s.replace(",", "")
        elif re.fullmatch(r"\d+,\d{1,2}", s):
            s = s.replace(",", ".")
        else:
            return None
    elif s.count(".") > 1:
        if not re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
            return None
        s = s.replace(".", "")
    try:
        v = Decimal(s)
    except InvalidOperation:
        return None
    return -v if neg else v


def detect_currency(text):
    """Explicit 3-letter code wins; unambiguous symbols next; bare '$' returns None."""
    for code in re.findall(r"\b([A-Z]{3})\b", text or ""):
        if code in CODES:
            return code
    for sym, code in SYMBOLS.items():
        if sym in (text or ""):
            return code
    return None


def parse_date(text, day_first=True):
    """Return (iso_date or None, ambiguous_bool)."""
    t = (text or "").strip()
    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", t)
    if m:
        return _mk(int(m[1]), int(m[2]), int(m[3])), False
    m = re.search(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", t)
    if m:
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        y += 2000 if y < 100 else 0
        if a > 12:
            return _mk(y, b, a), False
        if b > 12:
            return _mk(y, a, b), False
        return (_mk(y, b, a) if day_first else _mk(y, a, b)), a != b
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?[\s\-]+([A-Za-z]{3,9})[\s,\-]+(\d{4})", t)
    if m and m[2][:3].lower() in MONTHS:
        return _mk(int(m[3]), MONTHS[m[2][:3].lower()], int(m[1])), False
    m = re.search(r"([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", t)
    if m and m[1][:3].lower() in MONTHS:
        return _mk(int(m[3]), MONTHS[m[1][:3].lower()], int(m[2])), False
    return None, False


def _mk(y, m, d):
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def q(v):
    """Two-decimal string for storage/export."""
    return None if v is None else str(Decimal(v).quantize(Decimal("0.01")))
