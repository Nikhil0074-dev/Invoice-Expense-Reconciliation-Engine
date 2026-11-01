import os
import shutil

import pytest

S = os.path.join(os.path.dirname(__file__), "..", "..", "sample_data")


def up(client, name, data=None, ctype="application/pdf"):
    data = data if data is not None else open(os.path.join(S, name), "rb").read()
    return client.post("/api/documents", files={"file": (name, data, ctype)})


def test_rejects_bad_uploads(client):
    assert up(client, "x.pdf", b"MZ not a pdf").status_code == 415      # wrong content, right extension
    assert up(client, "x.pdf", b"").status_code == 422
    r = up(client, "x.pdf", b"%PDF-1.4 garbage")                          # malformed PDF
    assert r.json()["status"] == "failed"
    os.environ["MAX_UPLOAD_MB"] = "0"
    try:
        assert up(client, "invoice_abc_1042.pdf").status_code == 413
    finally:
        del os.environ["MAX_UPLOAD_MB"]


def test_extraction_flags_and_duplicate(client):
    r = up(client, "invoice_abc_1042.pdf").json()
    assert r["method"] == "pdf_text"
    inv = client.get("/api/invoices").json()[0]
    assert inv["invoice_number"] == "INV-1042" and inv["grand_total"] == "1180.00" and inv["currency"] == "INR"
    assert inv["flags"] == []
    up(client, "invoice_abc_1042_copy.pdf")
    types = {d["dtype"] for d in client.get("/api/duplicates").json()}
    assert {"exact_file", "duplicate_invoice"} <= types
    new_id = client.get("/api/invoices").json()[0]["id"]
    assert client.post(f"/api/invoices/{new_id}/approve", json={}).status_code == 409   # blocked by open duplicate
    for d in client.get("/api/duplicates").json():
        assert client.post(f"/api/duplicates/{d['id']}/resolve", json={"resolution": "confirmed_duplicate", "note": "same file"}).status_code == 200
    assert client.post(f"/api/invoices/{new_id}/approve", json={}).status_code == 200


def test_bad_totals_need_reason(client):
    up(client, "invoice_bad_totals.pdf")
    inv = client.get("/api/invoices").json()[0]
    assert "total_mismatch" in [f["code"] for f in inv["flags"]]
    assert client.post(f"/api/invoices/{inv['id']}/approve", json={}).status_code == 422
    assert client.patch(f"/api/invoices/{inv['id']}", json={"grand_total": "abc"}).status_code == 422
    fixed = client.patch(f"/api/invoices/{inv['id']}", json={"grand_total": "1180.00"}).json()
    assert fixed["flags"] == []
    assert client.post(f"/api/invoices/{inv['id']}/approve", json={}).status_code == 200
    assert client.patch(f"/api/invoices/{inv['id']}", json={"supplier_raw": "x"}).status_code == 409  # locked after approval
    actions = [a["action"] for a in client.get("/api/audit").json()]
    assert "edit" in actions and "approve" in actions


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract not installed")
def test_ocr_png(client):
    r = up(client, "receipt_greenleaf.png", ctype="image/png").json()
    assert r["method"] == "ocr"
    inv = client.get("/api/invoices").json()[0]
    assert inv["invoice_number"] == "GL-9001" and inv["grand_total"] == "945.00"


def test_bank_import_idempotent(client):
    f = open(os.path.join(S, "bank_transactions.csv"), "rb").read()
    r1 = client.post("/api/bank/import", files={"file": ("b.csv", f, "text/csv")}).json()
    r2 = client.post("/api/bank/import", files={"file": ("b.csv", f, "text/csv")}).json()
    assert r1["imported"] == 6 and r2["imported"] == 0 and r2["skipped_duplicates"] == 6
    bad = client.post("/api/bank/import", files={"file": ("b.csv", b"foo,bar\n1,2\n", "text/csv")})
    assert bad.status_code == 422
    rep = b"Date,Description,Amount\n01/10/2026,COFFEE,-5.00\n01/10/2026,COFFEE,-5.00\nnotadate,X,1\n"
    r = client.post("/api/bank/import", files={"file": ("r.csv", rep, "text/csv")}).json()
    assert r["imported"] == 2 and len(r["errors"]) == 1   # legit repeats kept, bad row reported


def test_full_workflow(client):
    for n in ("invoice_abc_1042.pdf", "invoice_office_2045.pdf", "invoice_bad_totals.pdf"):
        up(client, n)
    client.post("/api/bank/import", files={"file": ("b.csv", open(os.path.join(S, "bank_transactions.csv"), "rb").read(), "text/csv")})
    assert client.post("/api/reconcile/run").json()["suggestions"] >= 2
    ms = client.get("/api/reconcile/matches").json()
    by_inv = {}
    for m in ms:  # list is sorted best-first, so keep the first one per invoice
        by_inv.setdefault(m["invoice_number"], m)
    assert by_inv["INV-1042"]["band"] == "strong" and by_inv["INV-1042"]["ext_id"] is None
    assert all(m["invoice_number"] != "DC-77" for m in ms)          # total 1500 matches no transaction: no false match
    # same amount, wrong supplier must never be strong
    assert all(not (m["invoice_number"] == "2045" and m["description"] == "RANDOM VENDOR" and m["band"] == "strong") for m in ms)
    m1 = by_inv["INV-1042"]
    assert client.post(f"/api/reconcile/matches/{m1['id']}/approve").status_code == 200
    assert client.post(f"/api/reconcile/matches/{m1['id']}/approve").status_code == 409   # already reconciled
    invs = {i["invoice_number"]: i for i in client.get("/api/invoices").json()}
    for k in ("INV-1042", "2045"):
        client.post(f"/api/invoices/{invs[k]['id']}/approve", json={"reason": "checked"})
    pv = client.get("/api/exports/preview").json()
    assert pv["count"] == 2 and "BANK-92814" in pv["csv"] and pv["csv"].startswith("Date,Supplier")
    e = client.post("/api/exports").json()
    csv_text = client.get(f"/api/exports/{e['id']}/download").text
    assert csv_text.count("\n") == 3 and "1180.00" in csv_text
    assert client.post("/api/exports").status_code == 422            # no double export
    assert len(client.get("/api/exports").json()) == 1


def test_approved_txn_removed_from_other_suggestions(client):
    up(client, "invoice_abc_1042.pdf"); up(client, "invoice_abc_1042_copy.pdf")   # two invoices, same number and amount
    client.post("/api/bank/import", files={"file": ("b.csv", open(os.path.join(S, "bank_transactions.csv"), "rb").read(), "text/csv")})
    client.post("/api/reconcile/run")
    top = [m for m in client.get("/api/reconcile/matches").json() if m["description"].startswith("ABC TECH")][0]
    client.post(f"/api/reconcile/matches/{top['id']}/approve")
    left = [m for m in client.get("/api/reconcile/matches").json() if m["status"] == "suggested" and m["txn_id"] == top["txn_id"]]
    assert left == []
