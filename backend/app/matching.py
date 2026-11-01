"""Invoice-to-transaction matching. Always suggests, never auto-approves."""
import re
from datetime import date
from decimal import Decimal

from rapidfuzz import fuzz

WEIGHTS = {"amount": 0.40, "supplier": 0.25, "date": 0.15, "reference": 0.20}
BANDS = [(0.85, "strong"), (0.60, "possible"), (0.40, "weak")]
SUFFIX = r"\b(pvt|private|ltd|limited|inc|llc|corp|co|llp)\b"
NOISE = r"\b(online|payment|upi|neft|imps|pos|ach|card|purchase|txn|transfer|to|from)\b"


def norm_name(s):
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())
    s = re.sub(SUFFIX, " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_desc(s):
    return re.sub(r"\s+", " ", re.sub(NOISE, " ", norm_name(s))).strip()


def norm_id(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def band(score):
    for t, b in BANDS:
        if score >= t:
            return b
    return "none"


def score_pair(inv, txn):
    """inv/txn are dict-like. Returns (score, evidence) or None if a hard rule rejects it."""
    if not inv["currency"] or not txn["currency"] or inv["currency"] != txn["currency"]:
        return None  # hard rule: never compare different/unknown currencies
    ev, total = {}, Decimal(inv["grand_total"])
    amt = abs(Decimal(txn["amount"]))
    ev["amount"] = {"invoice": str(total), "transaction": str(amt), "exact": abs(amt - total) <= Decimal("0.01")}
    a = 1.0 if ev["amount"]["exact"] else 0.0
    ref = norm_id(inv["invoice_number"])
    hay = norm_id((txn["description"] or "") + (txn["reference"] or ""))
    r = 1.0 if len(ref) >= 3 and ref in hay else 0.0
    ev["reference"] = {"invoice_number": inv["invoice_number"], "found_in_bank_text": bool(r)}
    if not a and not r:
        return None  # amount differs and no reference: not a candidate (partial payments need allocations)
    s = max(fuzz.token_set_ratio(norm_name(inv["supplier_raw"]), txn["desc_norm"]),
            fuzz.partial_ratio(norm_name(inv["supplier_raw"]), txn["desc_norm"])) / 100 if inv["supplier_raw"] else 0.0
    ev["supplier"] = {"invoice": inv["supplier_raw"], "bank": txn["description"], "similarity": round(s, 2)}
    d = 0.0
    if inv["invoice_date"] and txn["tx_date"]:
        days = abs((date.fromisoformat(txn["tx_date"]) - date.fromisoformat(inv["invoice_date"])).days)
        d = max(0.0, 1 - days / 45)
        ev["date"] = {"invoice": inv["invoice_date"], "bank": txn["tx_date"], "days_apart": days}
    score = WEIGHTS["amount"] * a + WEIGHTS["supplier"] * s + WEIGHTS["date"] * d + WEIGHTS["reference"] * r
    ev["score_parts"] = {"amount": a, "supplier": round(s, 2), "date": round(d, 2), "reference": r}
    ev["model"] = "weights-v1 (unvalidated starting values)"
    return round(score, 3), ev
