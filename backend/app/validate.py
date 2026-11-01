from decimal import Decimal

TOL = Decimal("0.01")


def D(v):
    return None if v in (None, "") else Decimal(str(v))


def validate(inv: dict):
    """Return list of flags {code, severity, message}. Never changes values."""
    f = []
    add = lambda c, s, m: f.append({"code": c, "severity": s, "message": m})
    if not inv.get("supplier_raw"): add("missing_supplier", "warn", "Supplier not found")
    if not inv.get("invoice_number"): add("missing_invoice_number", "warn", "Invoice number not found")
    if not inv.get("invoice_date"): add("missing_date", "warn", "Invoice date not found")
    if not inv.get("currency"): add("missing_currency", "warn", "Currency unclear (a bare $ is never guessed)")
    if inv.get("date_ambiguous"): add("ambiguous_date", "warn", "Date could be day/month or month/day. Check it")
    gt = D(inv.get("grand_total"))
    if gt is None:
        add("missing_total", "error", "Grand total not found")
    elif gt <= 0:
        add("non_positive_total", "warn", "Total is zero or negative (credit note?)")
    sub, disc, tax, fees = (D(inv.get(k)) for k in ("subtotal", "discount", "tax", "fees"))
    lines = inv.get("line_items") or []
    if lines and sub is not None:
        s = sum(Decimal(l["line_total"]) for l in lines)
        if abs(s - sub) > TOL:
            add("line_sum_mismatch", "error", f"Line items add up to {s}, subtotal says {sub}")
    for l in lines:
        if abs(Decimal(l["quantity"]) * Decimal(l["unit_price"]) - Decimal(l["line_total"])) > TOL:
            add("line_math", "warn", f"Line '{l['description']}': qty x price != line total")
    if sub is not None and gt is not None:
        calc = sub - (disc or 0) + (tax or 0) + (fees or 0)
        if abs(calc - gt) > TOL:
            add("total_mismatch", "error", f"Subtotal - discount + tax + fees = {calc}, grand total says {gt}")
    return f
