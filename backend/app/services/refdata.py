"""Load the reference data the pure rules engine needs (vendors, POs, ledger, consumption)."""
import json
import sqlite3
from datetime import date

from app.rules.types import PO, Consumption, LedgerEntry, POLine, ReferenceData, Vendor


def load_reference(conn: sqlite3.Connection) -> ReferenceData:
    vendors = tuple(Vendor(**{k: r[k] for k in (
        "vendor_id", "legal_name", "gstin", "state_code", "bank_account_number", "ifsc",
        "trusted_contact_name", "trusted_contact_phone", "status")})
        for r in conn.execute("SELECT * FROM vendors ORDER BY vendor_id"))
    lines: dict[str, list[POLine]] = {}
    for r in conn.execute("SELECT * FROM po_lines ORDER BY po_number, line_no"):
        lines.setdefault(r["po_number"], []).append(POLine(
            r["po_line_id"], r["item_code"], r["description"], tuple(json.loads(r["aliases"])),
            float(r["quantity"]), r["unit_price"]))
    pos = tuple(PO(r["po_number"], r["vendor_id"], date.fromisoformat(r["po_date"]), r["po_total"],
                   tuple(lines.get(r["po_number"], ())))
                for r in conn.execute("SELECT * FROM purchase_orders ORDER BY po_number"))
    ledger = tuple(LedgerEntry(
        r["ledger_id"], r["run_id"], r["vendor_id"], r["invoice_number_raw"], r["invoice_number_normalised"],
        date.fromisoformat(r["invoice_date"]), r["total"], r["status"],
        tuple(sorted((s["key"], float(s["qty"])) for s in json.loads(r["line_signature"]))))
        for r in conn.execute("SELECT * FROM invoice_ledger ORDER BY invoice_date, ledger_id"))
    return ReferenceData(vendors, pos, ledger, load_consumption(conn))


def load_consumption(conn: sqlite3.Connection) -> Consumption:
    """Committed APPROVE consumption only: po_consumption is written solely by commit_approval
    and the seed loader (FR-069, FR-090)."""
    qty = {r[0]: float(r[1]) for r in conn.execute(
        "SELECT po_line_id, SUM(quantity) FROM po_consumption GROUP BY po_line_id")}
    value, invoices = {}, {}
    for r in conn.execute(
            "SELECT l.po_number, SUM(c.value), COUNT(DISTINCT c.ledger_id) FROM po_consumption c "
            "JOIN po_lines l ON l.po_line_id = c.po_line_id GROUP BY l.po_number"):
        value[r[0]], invoices[r[0]] = r[1], r[2]
    return Consumption(qty, value, invoices)
