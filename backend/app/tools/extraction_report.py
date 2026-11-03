"""Compare the shipped extraction snapshot with what each sample actually prints.

    python -m app.tools.extraction_report

Reads backend/seed/extraction_cache.json (DEC-014) and invoice-templates/manifest.json, and
prints wrong fields and fields below the critical-confidence threshold (A-17) per sample.
No provider calls.
"""
import json
import sys

from app.config import BACKEND_DIR
from app.services.extraction.schema import InvoiceExtraction

REPO = BACKEND_DIR.parent


def main() -> int:
    sys.path.insert(0, str(BACKEND_DIR))
    from tests.test_extraction_live import compare   # single source of the comparison rules

    snap = json.loads((BACKEND_DIR / "seed" / "extraction_cache.json").read_text(encoding="utf-8"))
    manifest = json.loads((REPO / "invoice-templates" / "manifest.json").read_text(encoding="utf-8"))
    by_file = {r["file_name"]: r for r in snap["records"]}
    missing = 0
    print(f"{'Test':6} {'model':18} {'wrong':>5} {'low':>4}  details")
    for inv in manifest["invoices"]:
        rec = by_file.get(inv["file"])
        if rec is None:
            missing += 1
            print(f"{inv['test_id']:6} NOT CACHED")
            continue
        wrong, low = compare(inv, InvoiceExtraction.model_validate(rec["payload"]))
        details = "; ".join(wrong + ["LOW " + x for x in low]) or "all fields exact"
        print(f"{inv['test_id']:6} {rec['model']:18} {len(wrong):5} {len(low):4}  {details}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
