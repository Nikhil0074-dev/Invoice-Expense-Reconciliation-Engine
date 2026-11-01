import json
from decimal import Decimal

from .matching import norm_id, norm_name


def add(c, doc_id, cand_id, dtype, score, evidence):
    c.execute("INSERT OR IGNORE INTO duplicates(doc_id,cand_doc_id,dtype,score,evidence) VALUES(?,?,?,?,?)",
              (doc_id, cand_id, dtype, score, json.dumps(evidence)))


def detect(c, inv_id):
    """Compare one invoice with all earlier ones. Flags candidates only; never declares fraud."""
    me = c.execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone()
    for o in c.execute("SELECT * FROM invoices WHERE id<?", (inv_id,)).fetchall():
        same_sup = me["supplier_norm"] and me["supplier_norm"] == o["supplier_norm"]
        if same_sup and me["invoice_number_norm"] and me["invoice_number_norm"] == o["invoice_number_norm"]:
            add(c, me["document_id"], o["document_id"], "duplicate_invoice", 1.0,
                {"supplier": me["supplier_raw"], "invoice_number": me["invoice_number"],
                 "note": "Same supplier and invoice number. Could be a corrected version or credit note."})
        elif same_sup and me["invoice_date"] == o["invoice_date"] and me["grand_total"] == o["grand_total"] \
                and me["grand_total"]:
            add(c, me["document_id"], o["document_id"], "potential_duplicate_expense", 0.8,
                {"supplier": me["supplier_raw"], "date": me["invoice_date"], "total": me["grand_total"],
                 "note": "Same supplier, date and total. Could be a legitimate repeat purchase."})
