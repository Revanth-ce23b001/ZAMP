"""Shared types for the rules engine (PRD §12.1: pure functions, no I/O).

Every check produces an Evidence item (FR-084), pass or fail. Reason codes are raised as Hits
that point at their evidence and carry the values used to render the §7.2 sentence (FR-085).
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Any

# Stage keys, in order (§5.3, §8.6).
VALIDATE, VENDOR_BANK, DUPLICATE, PO_MATCH, DECIDE = "VALIDATE", "VENDOR_BANK", "DUPLICATE", "PO_MATCH", "DECIDE"

# Check results and stage statuses (FR-083).
PASSED, FAILED, NEEDS_REVIEW, NOT_CHECKED, INFO = "PASSED", "FAILED", "NEEDS_REVIEW", "NOT_CHECKED", "INFO"


@dataclass
class Evidence:
    check: str
    stage: str
    source_fields: list[str]
    expected: Any
    actual: Any
    variance: Any
    allowed: Any
    result: str
    reason_code: str | None = None

    def as_dict(self) -> dict:
        return dict(self.__dict__)


class EvidenceLog:
    """Collects evidence for one run; stages append and keep the returned indexes."""

    def __init__(self):
        self.items: list[Evidence] = []

    def add(self, ev: Evidence) -> int:
        self.items.append(ev)
        return len(self.items) - 1


@dataclass
class Hit:
    code: str
    params: dict
    evidence: int            # index into the run's evidence list
    sentence: str | None = None   # explicit sentence when the PRD prescribes one (e.g. FR-021)


@dataclass
class StageOutcome:
    stage: str
    status: str
    summary: str
    hits: list[Hit] = field(default_factory=list)
    evidence_ids: list[int] = field(default_factory=list)
    not_checked_reason: str | None = None


@dataclass(frozen=True)
class Vendor:
    vendor_id: str
    legal_name: str
    gstin: str
    state_code: str
    bank_account_number: str
    ifsc: str
    trusted_contact_name: str
    trusted_contact_phone: str
    status: str


@dataclass(frozen=True)
class POLine:
    po_line_id: str
    item_code: str
    description: str
    aliases: tuple[str, ...]
    quantity: float
    unit_price: int          # paise


@dataclass(frozen=True)
class PO:
    po_number: str
    vendor_id: str
    po_date: date
    po_total: int            # paise
    lines: tuple[POLine, ...]


@dataclass(frozen=True)
class LedgerEntry:
    ledger_id: str
    run_id: str | None
    vendor_id: str
    invoice_number_raw: str
    invoice_number_normalised: str
    invoice_date: date
    total: int
    status: str
    line_signature: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class Consumption:
    """Committed APPROVE consumption only (FR-069)."""
    qty_by_line: dict[str, float]
    value_by_po: dict[str, int]
    invoices_by_po: dict[str, int]


@dataclass(frozen=True)
class ReferenceData:
    vendors: tuple[Vendor, ...]
    pos: tuple[PO, ...]
    ledger: tuple[LedgerEntry, ...]
    consumption: Consumption

    def po(self, number: str) -> PO | None:
        return next((p for p in self.pos if p.po_number == number), None)
