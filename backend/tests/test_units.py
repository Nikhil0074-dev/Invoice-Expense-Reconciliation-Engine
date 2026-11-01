from decimal import Decimal

from backend.app import export, matching, validate
from backend.app.extract import parse_invoice, sniff
from backend.app.money import detect_currency, parse_amount, parse_date


def test_amounts():
    assert parse_amount("1,234.56") == Decimal("1234.56")
    assert parse_amount("1.234,56") == Decimal("1234.56")
    assert parse_amount("1,23,456.00") == Decimal("123456.00")
    assert parse_amount("1234,56") == Decimal("1234.56")
    assert parse_amount("INR 1,180.00") == Decimal("1180.00")
    assert parse_amount("(50.00)") == Decimal("-50.00")
    assert parse_amount("abc") is None and parse_amount(None) is None


def test_dates():
    assert parse_date("10 October 2026") == ("2026-10-10", False)
    assert parse_date("2026-10-14") == ("2026-10-14", False)
    assert parse_date("25/10/2026") == ("2026-10-25", False)
    assert parse_date("12/10/2026") == ("2026-10-12", True)  # ambiguous, day-first
    assert parse_date("31/02/2026")[0] is None
    assert parse_date("nonsense")[0] is None


def test_currency():
    assert detect_currency("Total INR 10") == "INR"
    assert detect_currency("Price ₹10") == "INR"
    assert detect_currency("Total $10") is None  # ambiguous symbol never guessed


def test_parse_invoice_and_validate():
    t = "ABC Tech\nInvoice No: INV-1\nInvoice Date: 10 October 2026\nWidget 2 500.00 1000.00\nSubtotal 1000.00\nGST 180.00\nGrand Total INR 1,180.00"
    p = parse_invoice(t)
    assert p["supplier"] == "ABC Tech" and p["invoice_number"] == "INV-1"
    assert p["grand_total"] == Decimal("1180.00") and p["currency"] == "INR" and len(p["line_items"]) == 1
    inv = {"supplier_raw": p["supplier"], "invoice_number": "INV-1", "invoice_date": p["invoice_date"], "currency": "INR",
           "subtotal": "1000.00", "tax": "180.00", "grand_total": "1180.00", "line_items": p["line_items"]}
    assert validate.validate(inv) == []
    inv["grand_total"] = "1500.00"
    assert "total_mismatch" in [f["code"] for f in validate.validate(inv)]
    inv["grand_total"] = None
    assert "missing_total" in [f["code"] for f in validate.validate(inv)]


def test_missing_fields_not_invented():
    p = parse_invoice("hello world")
    assert p["invoice_number"] is None and p["grand_total"] is None and p["invoice_date"] is None


def test_sniff():
    assert sniff(b"%PDF-1.4") == "pdf" and sniff(b"\x89PNG\r\n\x1a\n") == "png"
    assert sniff(b"\xff\xd8\xff\xe0") == "jpeg" and sniff(b"MZ evil") is None


INV = {"currency": "INR", "grand_total": "1180.00", "invoice_number": "INV-1042", "supplier_raw": "ABC Technologies Pvt Ltd",
       "invoice_date": "2026-10-10"}
TX = {"currency": "INR", "amount": "-1180.00", "description": "ABC TECH ONLINE PAYMENT INV-1042", "reference": "B1",
      "desc_norm": matching.norm_desc("ABC TECH ONLINE PAYMENT INV-1042"), "tx_date": "2026-10-12"}


def test_match_strong_and_rules():
    s, ev = matching.score_pair(INV, TX)
    assert matching.band(s) == "strong" and ev["reference"]["found_in_bank_text"]
    assert matching.score_pair(INV, {**TX, "currency": "USD"}) is None  # currency hard rule
    assert matching.score_pair({**INV, "currency": None}, TX) is None
    assert matching.score_pair({**INV, "invoice_number": "X-9"}, {**TX, "amount": "-5.00"}) is None  # no amount, no ref


def test_same_amount_wrong_supplier_is_lower():
    other = {**TX, "description": "RANDOM VENDOR", "desc_norm": "random vendor", "reference": "B2"}
    s1, _ = matching.score_pair(INV, TX)
    s2, _ = matching.score_pair({**INV, "invoice_number": "ZZZ-1"}, other)
    assert s2 < s1 and matching.band(s2) != "strong"


def test_csv_safety_and_required():
    rows = [{"id": 1, "invoice_date": "2026-10-10", "supplier_raw": '=HYPERLINK("x")', "invoice_number": "A,1", "currency": "INR",
             "grand_total": "5.00", "category": "Line\nbreak"}, {"id": 2, "supplier_raw": "No date"}]
    text, errs, good = export.build_csv(rows)
    assert len(good) == 1 and errs[0]["invoice_id"] == 2
    assert "'=HYPERLINK" in text and '"A,1"' in text and '"Line\nbreak"' in text
    assert export.safe("-12.50") == "-12.50"
