import csv
from contextlib import asynccontextmanager
import hashlib
import io
import json
import os
import uuid
from decimal import Decimal

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import db, duplicates, export, extract, matching, validate
from .money import detect_currency, parse_amount, parse_date, q

@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    yield


app = FastAPI(title="Invoice & Expense Reconciliation Engine", lifespan=lifespan)
STATIC = os.path.join(os.path.dirname(__file__), "static")
FIELDS = ["supplier_raw", "invoice_number", "invoice_date", "due_date", "currency", "subtotal",
          "discount", "tax", "fees", "grand_total", "category"]
MONEY = {"subtotal", "discount", "tax", "fees", "grand_total"}


def rows(cur):
    return [dict(r) for r in cur.fetchall()]


def inv_out(r):
    d = dict(r)
    for k in ("line_items", "flags", "raw"):
        d[k] = json.loads(d[k] or "[]" if k != "raw" else d[k] or "{}")
    return d


def revalidate(c, inv_id):
    r = dict(c.execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone())
    r["line_items"] = json.loads(r["line_items"] or "[]")
    raw = json.loads(r["raw"] or "{}")
    r["date_ambiguous"] = raw.get("_date_ambiguous", False)
    c.execute("UPDATE invoices SET flags=? WHERE id=?", (json.dumps(validate.validate(r)), inv_id))


# ---------------- documents ----------------
@app.post("/api/documents")
async def upload(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > int(os.environ.get("MAX_UPLOAD_MB", "20")) * 1024 * 1024:
        raise HTTPException(413, "File too large")
    if not data:
        raise HTTPException(422, "Empty file")
    kind = extract.sniff(data)  # content check, not the file extension
    if not kind:
        raise HTTPException(415, "Only PDF, PNG and JPEG files are accepted")
    sha = hashlib.sha256(data).hexdigest()
    key = uuid.uuid4().hex
    store = os.environ.get("STORAGE_DIR", "data/files")
    os.makedirs(store, exist_ok=True)
    with open(os.path.join(store, key), "wb") as f:
        f.write(data)
    with db.session() as c:
        cur = c.execute("INSERT INTO documents(filename,sha256,kind,size,status,storage_key,uploaded_at) VALUES(?,?,?,?,?,?,?)",
                        (os.path.basename(file.filename or "upload")[:200], sha, kind, len(data), "extracting", key, db.now()))
        doc_id = cur.lastrowid
        db.audit(c, "upload", "document", doc_id, {"sha256": sha})
        same = c.execute("SELECT id FROM documents WHERE sha256=? AND id<?", (sha, doc_id)).fetchall()
        for s in same:
            duplicates.add(c, doc_id, s["id"], "exact_file", 1.0, {"sha256": sha, "note": "Byte-identical file"})
        try:
            text, method = extract.extract_text(data, kind)
        except extract.ExtractError as e:
            c.execute("UPDATE documents SET status='failed', error=? WHERE id=?", (str(e), doc_id))
            return {"id": doc_id, "status": "failed", "error": str(e)}
        c.execute("UPDATE documents SET text=?, method=?, status='awaiting_review' WHERE id=?", (text, method, doc_id))
        p = extract.parse_invoice(text)
        raw = dict(p["raw"]); raw["_date_ambiguous"] = p["date_ambiguous"]
        cur = c.execute(
            """INSERT INTO invoices(document_id,supplier_raw,supplier_norm,invoice_number,invoice_number_norm,
               invoice_date,due_date,currency,subtotal,discount,tax,fees,grand_total,line_items,raw)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (doc_id, p["supplier"], matching.norm_name(p["supplier"]), p["invoice_number"],
             matching.norm_id(p["invoice_number"]), p["invoice_date"], p["due_date"], p["currency"],
             *(q(p[k]) for k in ("subtotal", "discount", "tax", "fees", "grand_total")),
             json.dumps(p["line_items"]), json.dumps(raw)))
        inv_id = cur.lastrowid
        revalidate(c, inv_id)
        duplicates.detect(c, inv_id)
        db.audit(c, "extract", "invoice", inv_id, {"method": method})
    return {"id": doc_id, "invoice_id": inv_id, "status": "awaiting_review", "method": method}


@app.get("/api/documents")
def list_docs():
    with db.session() as c:
        return rows(c.execute("SELECT id,filename,kind,size,status,method,error,uploaded_at,sha256 FROM documents ORDER BY id DESC"))


@app.get("/api/documents/{doc_id}/file")
def doc_file(doc_id: int):
    with db.session() as c:
        d = c.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not d:
        raise HTTPException(404, "Not found")
    mime = {"pdf": "application/pdf", "png": "image/png", "jpeg": "image/jpeg"}[d["kind"]]
    return FileResponse(os.path.join(os.environ.get("STORAGE_DIR", "data/files"), d["storage_key"]), media_type=mime,
                        headers={"X-Content-Type-Options": "nosniff"})


# ---------------- invoices ----------------
@app.get("/api/invoices")
def list_invoices():
    with db.session() as c:
        return [inv_out(r) for r in c.execute("SELECT * FROM invoices ORDER BY id DESC").fetchall()]


@app.patch("/api/invoices/{inv_id}")
def patch_invoice(inv_id: int, body: dict = Body(...)):
    with db.session() as c:
        old = c.execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone()
        if not old:
            raise HTTPException(404, "Not found")
        if old["status"] == "approved":
            raise HTTPException(409, "Approved invoices cannot be edited")
        upd = {}
        for k, v in body.items():
            if k not in FIELDS:
                raise HTTPException(422, f"Field not editable: {k}")
            v = None if v in ("", None) else str(v).strip()
            if k in MONEY and v is not None:
                amt = parse_amount(v)
                if amt is None:
                    raise HTTPException(422, f"Invalid amount for {k}")
                v = q(amt)
            if k in ("invoice_date", "due_date") and v is not None:
                iso, _ = parse_date(v)
                if not iso:
                    raise HTTPException(422, f"Invalid date for {k}")
                v = iso
            if k == "currency" and v is not None:
                v = v.upper()
                if not (len(v) == 3 and v.isalpha()):
                    raise HTTPException(422, "Currency must be a 3-letter code")
            upd[k] = v
        if "supplier_raw" in upd:
            upd["supplier_norm"] = matching.norm_name(upd["supplier_raw"])
        if "invoice_number" in upd:
            upd["invoice_number_norm"] = matching.norm_id(upd["invoice_number"])
        if upd:
            c.execute(f"UPDATE invoices SET {','.join(k + '=?' for k in upd)} WHERE id=?", (*upd.values(), inv_id))
            if "invoice_date" in upd:  # user fixed the date: no longer ambiguous
                raw = json.loads(old["raw"] or "{}"); raw["_date_ambiguous"] = False
                c.execute("UPDATE invoices SET raw=? WHERE id=?", (json.dumps(raw), inv_id))
            revalidate(c, inv_id)
            db.audit(c, "edit", "invoice", inv_id, {k: {"old": old[k] if k in old.keys() else None, "new": v} for k, v in upd.items()})
        return inv_out(c.execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone())


@app.post("/api/invoices/{inv_id}/approve")
def approve(inv_id: int, body: dict = Body(default={})):
    with db.session() as c:
        r = c.execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Not found")
        flags = json.loads(r["flags"] or "[]")
        if any(f["code"] == "missing_total" for f in flags):
            raise HTTPException(422, "Cannot approve: grand total is missing")
        open_dup = c.execute("SELECT id FROM duplicates WHERE status='open' AND (doc_id=? OR cand_doc_id=?)",
                             (r["document_id"], r["document_id"])).fetchall()
        if open_dup:
            raise HTTPException(409, "Resolve the duplicate warning first")
        reason = (body.get("reason") or "").strip()
        if flags and not reason:
            raise HTTPException(422, "This invoice has warnings. Enter a reason to approve it anyway")
        c.execute("UPDATE invoices SET status='approved', approved_at=?, approval_reason=? WHERE id=?",
                  (db.now(), reason or None, inv_id))
        db.audit(c, "approve", "invoice", inv_id, {"reason": reason, "flags": [f["code"] for f in flags]})
    return {"ok": True}


# ---------------- bank ----------------
ALIASES = {"date": ["date", "transaction date", "txn date", "tran date", "value date"],
           "description": ["description", "narration", "details", "particulars", "memo"],
           "amount": ["amount", "signed amount"], "debit": ["debit", "withdrawal", "withdrawals", "dr"],
           "credit": ["credit", "deposit", "deposits", "cr"], "reference": ["reference", "ref no", "ref", "reference number", "utr"],
           "currency": ["currency"], "id": ["transaction id", "txn id", "id"]}


@app.post("/api/bank/import")
async def bank_import(file: UploadFile = File(...), currency: str = "INR", day_first: bool = True):
    raw = (await file.read())
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(422, "CSV must be UTF-8")
    rd = csv.DictReader(io.StringIO(text))
    if not rd.fieldnames:
        raise HTTPException(422, "CSV has no header row")
    cols = {}
    for key, names in ALIASES.items():
        for f in rd.fieldnames:
            if f.strip().lower() in names:
                cols[key] = f; break
    if "date" not in cols or "description" not in cols or ("amount" not in cols and "debit" not in cols and "credit" not in cols):
        raise HTTPException(422, "Need date, description and amount (or debit/credit) columns")
    imported = skipped = 0
    errors, seen = [], {}
    with db.session() as c:
        batch = c.execute("SELECT COALESCE(MAX(batch_id),0)+1 FROM transactions").fetchone()[0]
        for n, r in enumerate(rd, start=2):
            iso, _ = parse_date(r.get(cols["date"]), day_first)
            if "amount" in cols:
                amt = parse_amount(r.get(cols["amount"]))
            else:
                dr, cr = parse_amount(r.get(cols.get("debit", ""))), parse_amount(r.get(cols.get("credit", "")))
                amt = -abs(dr) if dr else (abs(cr) if cr else None)
            if not iso or amt is None:
                errors.append({"row": n, "error": "Bad date or amount"}); continue
            cur_code = (r.get(cols.get("currency", "")) or currency).strip().upper()
            desc = (r.get(cols["description"]) or "").strip()
            base = f"{iso}|{desc}|{q(amt)}|{cur_code}"
            seen[base] = seen.get(base, 0) + 1  # identical rows in one file are legitimate repeats
            key = hashlib.sha256(f"{base}|{seen[base]}".encode()).hexdigest()
            try:
                c.execute("INSERT INTO transactions(ext_id,tx_date,description,desc_norm,amount,currency,reference,batch_id,dedupe_key) VALUES(?,?,?,?,?,?,?,?,?)",
                          ((r.get(cols.get("id", "")) or "").strip() or None, iso, desc, matching.norm_desc(desc), q(amt),
                           cur_code, (r.get(cols.get("reference", "")) or "").strip() or None, batch, key))
                imported += 1
            except Exception:
                skipped += 1
        db.audit(c, "bank_import", "transactions", batch, {"imported": imported, "skipped": skipped, "errors": len(errors)})
    return {"imported": imported, "skipped_duplicates": skipped, "errors": errors}


@app.get("/api/bank/transactions")
def txns():
    with db.session() as c:
        return rows(c.execute("""SELECT t.*, (SELECT 1 FROM matches m WHERE m.txn_id=t.id AND m.status='approved') AS reconciled
                                 FROM transactions t ORDER BY tx_date DESC, id DESC"""))


# ---------------- reconciliation ----------------
@app.post("/api/reconcile/run")
def run():
    n = 0
    with db.session() as c:
        c.execute("DELETE FROM matches WHERE status='suggested'")
        used_txn = {r[0] for r in c.execute("SELECT txn_id FROM matches WHERE status='approved'")}
        done_inv = {r[0] for r in c.execute("SELECT invoice_id FROM matches WHERE status='approved'")}
        txs = [dict(t) for t in c.execute("SELECT * FROM transactions").fetchall() if t["id"] not in used_txn]
        for inv in c.execute("SELECT * FROM invoices WHERE grand_total IS NOT NULL").fetchall():
            if inv["id"] in done_inv:
                continue
            cands = []
            for t in txs:
                res = matching.score_pair(dict(inv), t)
                if res and matching.band(res[0]) != "none":
                    cands.append((res[0], t["id"], res[1]))
            cands.sort(key=lambda x: -x[0])
            for i, (s, tid, ev) in enumerate(cands[:3]):
                if i == 0 and len(cands) > 1 and cands[1][0] >= s - 0.05:
                    ev["warning"] = "Another transaction scores almost the same. Check carefully."
                c.execute("INSERT INTO matches(invoice_id,txn_id,score,band,evidence) VALUES(?,?,?,?,?)",
                          (inv["id"], tid, s, matching.band(s), json.dumps(ev)))
                n += 1
        db.audit(c, "reconcile_run", "matches", 0, {"suggestions": n})
    return {"suggestions": n}


@app.get("/api/reconcile/matches")
def matches():
    with db.session() as c:
        out = rows(c.execute("""SELECT m.*, i.supplier_raw, i.invoice_number, i.grand_total, i.currency,
            t.description, t.tx_date, t.amount, t.ext_id FROM matches m JOIN invoices i ON i.id=m.invoice_id
            JOIN transactions t ON t.id=m.txn_id ORDER BY m.status='approved' DESC, m.score DESC"""))
    for r in out:
        r["evidence"] = json.loads(r["evidence"])
    return out


@app.post("/api/reconcile/matches/{mid}/{action}")
def decide(mid: int, action: str):
    if action not in ("approve", "reject"):
        raise HTTPException(404, "Unknown action")
    with db.session() as c:
        m = c.execute("SELECT * FROM matches WHERE id=?", (mid,)).fetchone()
        if not m:
            raise HTTPException(404, "Not found")
        if action == "approve":
            clash = c.execute("SELECT id FROM matches WHERE status='approved' AND (invoice_id=? OR txn_id=?)",
                              (m["invoice_id"], m["txn_id"])).fetchone()
            if clash:
                raise HTTPException(409, "Invoice or transaction is already reconciled")
            c.execute("UPDATE matches SET status='approved', decided_at=? WHERE id=?", (db.now(), mid))
            c.execute("UPDATE matches SET status='rejected' WHERE invoice_id=? AND status='suggested'", (m["invoice_id"],))
            c.execute("UPDATE matches SET status='rejected' WHERE txn_id=? AND status='suggested'", (m["txn_id"],))
        else:
            c.execute("UPDATE matches SET status='rejected', decided_at=? WHERE id=?", (db.now(), mid))
        db.audit(c, action, "match", mid, {"invoice_id": m["invoice_id"], "txn_id": m["txn_id"], "score": m["score"]})
    return {"ok": True}


# ---------------- duplicates ----------------
@app.get("/api/duplicates")
def dups():
    with db.session() as c:
        out = rows(c.execute("""SELECT d.*, a.filename AS file_a, b.filename AS file_b FROM duplicates d
            JOIN documents a ON a.id=d.doc_id JOIN documents b ON b.id=d.cand_doc_id ORDER BY d.status='open' DESC, d.id DESC"""))
    for r in out:
        r["evidence"] = json.loads(r["evidence"])
    return out


@app.post("/api/duplicates/{did}/resolve")
def resolve(did: int, body: dict = Body(...)):
    st = body.get("resolution")
    if st not in ("confirmed_duplicate", "distinct"):
        raise HTTPException(422, "resolution must be confirmed_duplicate or distinct")
    with db.session() as c:
        if not c.execute("SELECT 1 FROM duplicates WHERE id=?", (did,)).fetchone():
            raise HTTPException(404, "Not found")
        c.execute("UPDATE duplicates SET status=?, note=? WHERE id=?", (st, body.get("note"), did))
        db.audit(c, "resolve_duplicate", "duplicate", did, {"resolution": st, "note": body.get("note")})
    return {"ok": True}


# ---------------- export ----------------
def export_rows(c):
    return rows(c.execute("""SELECT i.*, (SELECT COALESCE(t.reference, t.ext_id) FROM matches m JOIN transactions t ON t.id=m.txn_id
        WHERE m.invoice_id=i.id AND m.status='approved') AS txn_ref FROM invoices i
        WHERE i.status='approved' AND i.exported_at IS NULL ORDER BY i.id"""))


@app.get("/api/exports/preview")
def preview():
    with db.session() as c:
        text, errs, good = export.build_csv(export_rows(c))
    return {"csv": text, "errors": errs, "count": len(good)}


@app.post("/api/exports")
def make_export():
    with db.session() as c:
        text, errs, good = export.build_csv(export_rows(c))
        if not good:
            raise HTTPException(422, {"message": "Nothing to export", "errors": errs})
        name = f"export_{db.now().replace(':', '-')}.csv"
        cur = c.execute("INSERT INTO exports(created_at,record_count,filename,content) VALUES(?,?,?,?)",
                        (db.now(), len(good), name, text))
        for r in good:
            c.execute("UPDATE invoices SET exported_at=? WHERE id=?", (db.now(), r["id"]))
        db.audit(c, "export", "export", cur.lastrowid, {"records": len(good), "skipped": errs})
    return {"id": cur.lastrowid, "filename": name, "count": len(good), "errors": errs}


@app.get("/api/exports")
def exports_list():
    with db.session() as c:
        return rows(c.execute("SELECT id,created_at,record_count,filename FROM exports ORDER BY id DESC"))


@app.get("/api/exports/{eid}/download")
def download(eid: int):
    with db.session() as c:
        e = c.execute("SELECT * FROM exports WHERE id=?", (eid,)).fetchone()
    if not e:
        raise HTTPException(404, "Not found")
    return Response(e["content"], media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{e["filename"]}"'})


@app.get("/api/audit")
def audit_log():
    with db.session() as c:
        return rows(c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 200"))


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))
