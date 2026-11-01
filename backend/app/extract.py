"""Text extraction (digital PDF first, OCR fallback) and invoice field parsing."""
import io
import os
import re
from decimal import Decimal

from .money import detect_currency, parse_amount, parse_date

NUM = r"[-\d][\d,.\s]*\d|\d"


class ExtractError(Exception):
    pass


def sniff(data: bytes):
    if data.startswith(b"%PDF"):
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    return None


def ocr_image(img):
    try:
        import pytesseract
        return pytesseract.image_to_string(img, lang=os.environ.get("OCR_LANGUAGES", "eng"))
    except Exception as e:  # missing binary or language data
        raise ExtractError(f"OCR failed: {e}")


def extract_text(data: bytes, kind: str):
    """Returns (text, method). Raises ExtractError for unreadable/encrypted files."""
    if kind == "pdf":
        from pypdf import PdfReader
        try:
            r = PdfReader(io.BytesIO(data))
        except Exception as e:
            raise ExtractError(f"Malformed PDF: {e}")
        if r.is_encrypted:
            raise ExtractError("Encrypted PDFs are not supported")
        if len(r.pages) > int(os.environ.get("MAX_PAGES", "30")):
            raise ExtractError("Too many pages")
        text = "\n".join((p.extract_text() or "") for p in r.pages)
        if len(text.strip()) >= 20:
            return text, "pdf_text"
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(data)
        out = [ocr_image(pdf[i].render(scale=3).to_pil()) for i in range(len(pdf))]
        return "\n".join(out), "ocr"
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        raise ExtractError(f"Malformed image: {e}")
    return ocr_image(img), "ocr"


def _last_amount(line):
    nums = re.findall(NUM, line)
    for n in reversed(nums):
        v = parse_amount(n)
        if v is not None:
            return v, n.strip()
    return None, None


def parse_invoice(text: str):
    """Return dict of fields. Missing fields are None - never guessed."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    out = {k: None for k in ("supplier", "invoice_number", "invoice_date", "due_date", "currency",
                              "subtotal", "discount", "tax", "fees", "grand_total")}
    out["line_items"], out["raw"], out["date_ambiguous"] = [], {}, False
    skip = re.compile(r"^(tax\s+)?invoice|^receipt|^credit note|^bill\b", re.I)
    for l in lines[:6]:
        m = re.match(r"(?:from|supplier|vendor)\s*:\s*(.+)", l, re.I)
        if m:
            out["supplier"] = m[1].strip(); break
    if not out["supplier"]:
        for l in lines[:6]:
            if not skip.match(l) and re.search(r"[A-Za-z]{3}", l):
                out["supplier"] = l; break
    m = re.search(r"(?:invoice|inv|bill)\s*(?:no\.?|number|#)\s*[:#\-]?\s*([A-Za-z0-9][A-Za-z0-9\-/]*)", text, re.I)
    if m:
        out["invoice_number"] = m[1]
    labels = [("grand_total", r"(grand\s*total|total\s*due|amount\s*due|total\s*amount|^total)"),
              ("subtotal", r"(sub\s*-?\s*total)"), ("discount", r"(discount)"),
              ("tax", r"(gst|vat|tax|igst|cgst|sgst)"), ("fees", r"(shipping|fee|handling)")]
    gt_rank = None
    items_started = False
    for l in lines:
        low = l.lower()
        if low.startswith("due date"):
            out["raw"]["due_date"] = l
            out["due_date"], _ = parse_date(l)
            continue
        if low.startswith("invoice date") or low.startswith("date"):
            out["raw"]["invoice_date"] = l
            out["invoice_date"], out["date_ambiguous"] = parse_date(l)
            continue
        matched = False
        for key, pat in labels:
            if re.search(pat, low) and not (key == "tax" and "total" in low and "grand" in low):
                if key == "grand_total" and re.search(r"sub\s*-?\s*total", low):
                    continue
                v, raw = _last_amount(l)
                if v is None:
                    continue
                if key == "grand_total":
                    rank = 0 if re.search(r"grand|due", low) else 1
                    if gt_rank is None or rank < gt_rank:
                        gt_rank, out[key] = rank, v
                        out["raw"][key] = l
                elif out[key] is None:
                    out[key] = v; out["raw"][key] = l
                matched = True
                break
        if matched:
            continue
        m = re.match(rf"^(.+?)\s+(\d+(?:\.\d+)?)\s+({NUM})\s+({NUM})$", l)
        if m and re.search(r"[A-Za-z]", m[1]) and not re.match(r"(qty|description|item)", m[1], re.I):
            up, lt = parse_amount(m[3]), parse_amount(m[4])
            if up is not None and lt is not None:
                out["line_items"].append({"description": m[1].strip(), "quantity": m[2],
                                          "unit_price": str(up), "line_total": str(lt), "raw": l})
    out["currency"] = detect_currency(text)
    return out
