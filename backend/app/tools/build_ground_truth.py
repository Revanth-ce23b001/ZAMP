"""Build backend/seed/extraction_ground_truth.json: what each sample PDF actually prints.

    python -m app.tools.build_ground_truth

Ground truth comes only from the sample PDFs' printed content, never from a model's output:
  - Printed values (invoice number, date, vendor, GSTIN, PO reference, amounts, lines) come from
    invoice-templates/manifest.json, which generate.py renders verbatim into backend/samples/ and
    verify.py checks against SHA256SUMS.
  - Document type and currency are not manifest fields. They are read from the static text of the
    sample's layout template. Where the layout prints no document title (or no currency marker),
    the expected value cannot be established from the PDF and is recorded as unresolved.
Expected decisions come from PRD §13, as copied into tests/test_scenarios.py EXPECTED.
Deterministic: re-running produces an identical file.
"""
import hashlib
import json
import re
import sys

from app.config import BACKEND_DIR
from app.utils.money import rupees_to_paise

REPO = BACKEND_DIR.parent
SAMPLES = BACKEND_DIR / "samples"
MANIFEST = REPO / "invoice-templates" / "manifest.json"
LAYOUTS = REPO / "invoice-templates" / "layouts"
OUT = BACKEND_DIR / "seed" / "extraction_ground_truth.json"

FIELDS = ("invoice_number", "invoice_date", "vendor_name", "vendor_gstin", "po_number", "currency",
          "document_type", "subtotal_paise", "tax_total_paise", "invoice_total_paise", "line_count")

# PRD §13 expected outcomes, in the PRD run order (identical to tests/test_scenarios.py EXPECTED;
# tests/test_accuracy.py asserts the two never drift apart).
EXPECTED_DECISIONS = [
    ("T-01", "APPROVE", []), ("T-02", "HOLD", ["QUANTITY_EXCEEDS_PO", "PO_BALANCE_EXCEEDED"]),
    ("T-03", "HOLD", ["BANK_DETAILS_MISMATCH"]), ("T-04", "HOLD", ["PO_INFERRED"]),
    ("T-05", "REJECT", ["CONFIRMED_DUPLICATE"]), ("T-06", "APPROVE", []), ("T-07", "HOLD", ["POSSIBLE_REVISION"]),
    ("T-08", "HOLD", ["UNIT_PRICE_VARIANCE"]), ("T-09", "HOLD", ["VENDOR_NOT_FOUND"]),
    ("T-12", "HOLD", ["LINE_ITEM_UNMATCHED"]), ("T-15", "HOLD", ["ARITHMETIC_MISMATCH"]),
    ("T-16", "HOLD", ["POSSIBLE_DUPLICATE"]), ("T-17", "HOLD", ["PO_INFERRED"]),
    ("T-18", "REJECT", ["VENDOR_BLOCKED"]), ("T-25a", "HOLD", ["BANK_DETAILS_MISMATCH"]), ("T-25b", "APPROVE", []),
]


def _static_text(html: str) -> str:
    """Text a layout prints regardless of the invoice: Jinja expressions/blocks and the <head> removed."""
    html = re.sub(r"<head>.*?</head>", " ", html, flags=re.S | re.I)
    html = re.sub(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", " ", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", html)


def layout_facts(layout: str) -> dict:
    text = _static_text((LAYOUTS / f"{layout}.html").read_text(encoding="utf-8"))
    title = re.search(r"\btax\s+invoice\b", text, re.I)
    if "₹" in text:
        currency = ("INR", f"PDF: layout '{layout}' prints the rupee sign (₹) in its column headings")
    elif re.search(r"\bINR\b", text):
        line = next(l.strip() for l in text.splitlines() if re.search(r"\bINR\b", l))
        currency = ("INR", f"PDF: layout '{layout}' prints \"{line}\"")
    else:
        currency = (None, f"unresolved: layout '{layout}' prints no currency marker (no ₹, no INR)")
    if title:
        document_type = ("tax_invoice", f"PDF: layout '{layout}' prints the title \"{title.group(0)}\"")
    else:
        heading = re.search(r"\bINVOICE\b", text)
        why = "prints only the heading \"INVOICE\"" if heading else "prints no document title"
        document_type = (None, f"unresolved: layout '{layout}' {why}; 'Tax Invoice' is not printed")
    return {"currency": currency, "document_type": document_type}


def entry(inv: dict) -> dict:
    printed = f"PDF: printed value (invoice-templates/manifest.json {inv['test_id']}, rendered verbatim by generate.py)"
    facts = layout_facts(inv["layout"])

    def known(value, source=printed):
        return {"expected": value, "source": source}

    def from_layout(key):
        value, source = facts[key]
        return {"expected": value, "source": source if value is not None else "unresolved",
                **({"note": source} if value is None else {})}

    tax = [inv["tax"][k]["amount"] for k in ("cgst", "sgst", "igst") if inv["tax"].get(k)]
    po = inv["po_reference"]
    return {
        "test_id": inv["test_id"], "file": inv["file"], "layout": inv["layout"], "scan": bool(inv.get("scan")),
        "fields": {
            "invoice_number": known(inv["invoice_number"]),
            "invoice_date": known(inv["invoice_date"]),
            "vendor_name": known(inv["vendor"]["legal_name"]),
            "vendor_gstin": known(inv["vendor"]["gstin"]),
            "po_number": known(po, printed if po else f"PDF: no PO reference printed ({inv['test_id']} manifest po_reference is null)"),
            "currency": from_layout("currency"),
            "document_type": from_layout("document_type"),
            "subtotal_paise": known(rupees_to_paise(inv["subtotal"])),
            "tax_total_paise": known(sum(rupees_to_paise(a) for a in tax),
                                     printed + "; sum of the printed CGST/SGST/IGST amounts"),
            "invoice_total_paise": known(rupees_to_paise(inv["total"])),
            "line_count": known(len(inv["lines"]), printed + "; printed line items (other charges excluded)"),
        },
    }


def build() -> dict:
    manifest = {i["file"]: i for i in json.loads(MANIFEST.read_text(encoding="utf-8"))["invoices"]}
    samples = {}
    for pdf in sorted(SAMPLES.glob("*.pdf")):
        samples[hashlib.sha256(pdf.read_bytes()).hexdigest()] = entry(manifest[pdf.name])
    by_id = {s["test_id"]: s for s in samples.values()}
    return {
        "_meta": {
            "kind": "extraction ground truth (Day 8, DEC-018). Generated by python -m app.tools.build_ground_truth; never hand-edited.",
            "keyed_by": "SHA-256 of the sample PDF, the same key the extraction cache uses",
            "fields": list(FIELDS),
            "money": "integer paise",
            "unresolved_rule": "expected is null and source is 'unresolved' when the value cannot be established from the PDF; "
                               "such fields are excluded from accuracy numerator and denominator",
            "expected_decisions_source": "PRD v1.2.1 §13 (copied exactly into backend/tests/test_scenarios.py EXPECTED)",
        },
        "samples": samples,
        "expected_decisions": [
            {"test_id": t, "file": by_id[t]["file"], "status": s, "reason_codes": c,
             "source": f"PRD §13 {t.rstrip('ab')}" + (" (a)" if t.endswith("a") else " (b)" if t.endswith("b") else "")}
            for t, s, c in EXPECTED_DECISIONS],
    }


def main() -> int:
    OUT.write_text(json.dumps(build(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
