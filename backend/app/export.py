import csv
import io

DEFAULT_PROFILE = {
    "name": "generic",
    "columns": [("Date", "invoice_date"), ("Supplier", "supplier_raw"), ("Invoice Number", "invoice_number"),
                ("Category", "category"), ("Currency", "currency"), ("Amount", "subtotal"), ("Tax", "tax"),
                ("Total", "grand_total"), ("Transaction Reference", "txn_ref")],
    "required": ["invoice_date", "supplier_raw", "invoice_number", "currency", "grand_total"],
}


def safe(v):
    """Stop spreadsheet formula injection in text cells."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") and not _is_number(s) else s


def _is_number(s):
    try:
        float(s); return True
    except ValueError:
        return False


def build_csv(rows, profile=None):
    """rows: list of dicts. Returns (csv_text, errors). Rows missing required fields are skipped and reported."""
    p = profile or DEFAULT_PROFILE
    errors, good = [], []
    for r in rows:
        miss = [k for k in p["required"] if not r.get(k)]
        if miss:
            errors.append({"invoice_id": r.get("id"), "missing": miss})
        else:
            good.append(r)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([h for h, _ in p["columns"]])
    for r in good:
        w.writerow([safe(r.get(k)) for _, k in p["columns"]])
    return buf.getvalue(), errors, good
