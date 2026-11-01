import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY, filename TEXT, sha256 TEXT, kind TEXT, size INT,
  doc_type TEXT, status TEXT, method TEXT, error TEXT, text TEXT, storage_key TEXT, uploaded_at TEXT);
CREATE TABLE IF NOT EXISTS invoices(id INTEGER PRIMARY KEY, document_id INT REFERENCES documents(id),
  supplier_raw TEXT, supplier_norm TEXT, invoice_number TEXT, invoice_number_norm TEXT, invoice_date TEXT,
  due_date TEXT, currency TEXT, subtotal TEXT, discount TEXT, tax TEXT, fees TEXT, grand_total TEXT,
  category TEXT, line_items TEXT, raw TEXT, flags TEXT, status TEXT DEFAULT 'needs_review',
  approved_at TEXT, approval_reason TEXT, exported_at TEXT);
CREATE TABLE IF NOT EXISTS transactions(id INTEGER PRIMARY KEY, ext_id TEXT, tx_date TEXT, description TEXT,
  desc_norm TEXT, amount TEXT, currency TEXT, reference TEXT, batch_id INT, dedupe_key TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS matches(id INTEGER PRIMARY KEY, invoice_id INT, txn_id INT, score REAL, band TEXT,
  evidence TEXT, status TEXT DEFAULT 'suggested', decided_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS one_inv ON matches(invoice_id) WHERE status='approved';
CREATE UNIQUE INDEX IF NOT EXISTS one_txn ON matches(txn_id) WHERE status='approved';
CREATE TABLE IF NOT EXISTS duplicates(id INTEGER PRIMARY KEY, doc_id INT, cand_doc_id INT, dtype TEXT,
  score REAL, evidence TEXT, status TEXT DEFAULT 'open', note TEXT, UNIQUE(doc_id, cand_doc_id, dtype));
CREATE TABLE IF NOT EXISTS exports(id INTEGER PRIMARY KEY, created_at TEXT, record_count INT, filename TEXT, content TEXT);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, ts TEXT, user TEXT, action TEXT, resource TEXT, rid INT, detail TEXT);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init():
    p = os.environ.get("DB_PATH", "data/app.db")
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    c = sqlite3.connect(p)
    c.executescript(SCHEMA)
    c.commit(); c.close()


@contextmanager
def session():
    c = sqlite3.connect(os.environ.get("DB_PATH", "data/app.db"))
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback(); raise
    finally:
        c.close()


def audit(c, action, resource, rid, detail=None, user="local"):
    c.execute("INSERT INTO audit(ts,user,action,resource,rid,detail) VALUES(?,?,?,?,?,?)",
              (now(), user, action, resource, rid, json.dumps(detail or {}, default=str)))
