"""Creates SYNTHETIC sample invoices (PDF + PNG) and a bank CSV. No real data."""
import os
import sys

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "sample_data")
os.makedirs(OUT, exist_ok=True)

INVOICES = {
    "invoice_abc_1042.pdf": ["ABC Technologies Pvt Ltd", "TAX INVOICE", "Invoice No: INV-1042", "Invoice Date: 10 October 2026",
        "Due Date: 25 October 2026", "Description Qty Unit Price Total", "Software Subscription 1 1000.00 1000.00",
        "Subtotal 1000.00", "GST 18% 180.00", "Grand Total INR 1,180.00"],
    "invoice_office_2045.pdf": ["Office Supplies Ltd", "INVOICE", "Invoice No: 2045", "Invoice Date: 12/10/2026",
        "Description Qty Unit Price Total", "Stationery Pack 3 250.00 750.00", "Subtotal 750.00", "GST 135.00", "Total Due INR 885.00"],
    "invoice_bad_totals.pdf": ["Delta Cloud Services", "INVOICE", "Invoice No: DC-77", "Invoice Date: 2026-10-14",
        "Hosting 2 500.00 1000.00", "Subtotal 1000.00", "GST 180.00", "Grand Total INR 1,500.00"],
}
for name, lines in INVOICES.items():
    c = canvas.Canvas(os.path.join(OUT, name), pagesize=A4)
    y = 800
    for l in lines:
        c.drawString(60, y, l); y -= 24
    c.save()
# exact duplicate upload of the first invoice
with open(os.path.join(OUT, "invoice_abc_1042.pdf"), "rb") as a, open(os.path.join(OUT, "invoice_abc_1042_copy.pdf"), "wb") as b:
    b.write(a.read())

# scanned-style PNG (needs OCR)
font = None
for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf"):
    if os.path.exists(p):
        font = ImageFont.truetype(p, 34); break
img = Image.new("RGB", (1400, 700), "white")
d = ImageDraw.Draw(img)
for i, l in enumerate(["Green Leaf Cafe", "RECEIPT", "Invoice No: GL-9001", "Date: 2026-10-20",
                       "Team Lunch 2 450.00 900.00", "Subtotal 900.00", "GST 45.00", "Total INR 945.00"]):
    d.text((60, 40 + i * 70), l, fill="black", font=font)
img.save(os.path.join(OUT, "receipt_greenleaf.png"))

with open(os.path.join(OUT, "bank_transactions.csv"), "w", encoding="utf-8") as f:
    f.write("Date,Description,Debit,Credit,Reference\n"
            "12/10/2026,ABC TECH ONLINE PAYMENT INV-1042,1180.00,,BANK-92814\n"
            "13/10/2026,OFFICE SUPPLIES LTD NEFT,885.00,,BANK-92829\n"
            "14/10/2026,DELTA CLOUD,1180.00,,BANK-92833\n"
            "21/10/2026,GREEN LEAF CAFE POS,945.00,,BANK-92850\n"
            "22/10/2026,RANDOM VENDOR,885.00,,BANK-92861\n"
            "23/10/2026,SALARY CREDIT,,50000.00,BANK-92870\n")
print("samples written to", OUT)
