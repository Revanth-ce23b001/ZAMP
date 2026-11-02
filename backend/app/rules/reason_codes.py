"""Canonical reason-code registry (PRD v1.2.1 §7.2, FR-081).

This is the only place reason codes are defined. Business logic must reference
codes from REGISTRY and must never create codes ad hoc.
Templates and next actions are copied verbatim from PRD §7.2; {placeholders} are
filled with real values when a decision is rendered (FR-085).
"""
from dataclasses import dataclass
from enum import Enum


class Severity(str, Enum):
    REJECT = "REJECT"
    HOLD = "HOLD"


class StageStatus(str, Enum):
    FAILED = "FAILED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


# Processing stages that can emit reason codes (stage number -> stage key used in runs.stage_results).
STAGES = {3: "VALIDATE", 4: "VENDOR_BANK", 5: "DUPLICATE", 6: "PO_MATCH"}


@dataclass(frozen=True)
class ReasonCode:
    code: str
    severity: Severity
    stage: int
    stage_status: StageStatus
    template: str
    next_action: str
    priority: str  # "P0" or "P1"

    @property
    def stage_key(self) -> str:
        return STAGES[self.stage]


_R, _H = Severity.REJECT, Severity.HOLD
_F, _N = StageStatus.FAILED, StageStatus.NEEDS_REVIEW

_CODES = (
    ReasonCode("CONFIRMED_DUPLICATE", _R, 5, _F,
               "Same vendor and invoice number as {orig_no} ({orig_date}, {orig_status}).",
               "Do not pay. Notify vendor if resubmitted in error.", "P0"),
    ReasonCode("POSSIBLE_DUPLICATE", _H, 5, _N,
               "Matches {orig_no}: same total {total}, {days} days apart, same lines.",
               "Compare side by side; confirm or mark as duplicate (Reject).", "P0"),
    ReasonCode("POSSIBLE_REVISION", _H, 5, _N,
               "Possible revised version of {orig_no} ({orig_status}).",
               "Confirm whether it replaces the original.", "P0"),
    ReasonCode("VENDOR_BLOCKED", _R, 4, _F,
               "Vendor {name} is blocked in the vendor master.",
               "Return to vendor; escalate to procurement if disputed.", "P0"),
    ReasonCode("VENDOR_NOT_FOUND", _H, 4, _N,
               "No vendor in master matches GSTIN {gstin} / name {name}.",
               "Route to vendor onboarding.", "P0"),
    ReasonCode("BANK_DETAILS_MISMATCH", _H, 4, _N,
               "Bank details {masked_inv} differ from vendor master {masked_master}.",
               "Verify with {contact} via contact on record. Never use details on the invoice.", "P0"),
    ReasonCode("MISSING_REQUIRED_FIELD", _H, 3, _F,
               "Missing required field: {field}.",
               "Check the PDF; request a corrected invoice.", "P0"),
    ReasonCode("LOW_EXTRACTION_CONFIDENCE", _H, 3, _N,
               "{field} extracted with confidence {c} (threshold 0.85).",
               "Check the PDF; extracted value may be wrong.", "P0"),
    # PRD lists the priority as "P1 (rule)": schema fields are P0, the rule is P1 (OD-7).
    ReasonCode("UNSUPPORTED_DOCUMENT", _H, 3, _F,
               "Document type {type} / currency {cur} is not supported.",
               "Process manually.", "P1"),
    ReasonCode("PO_NOT_FOUND", _H, 6, _N,
               "No purchase order found for this vendor{ref}.",
               "Find the PO or ask the requester; confirm a suggestion if shown.", "P0"),
    ReasonCode("PO_INFERRED", _H, 6, _N,
               "No PO on invoice. Suggested: {po} (score {s}).",
               "Confirm the suggested PO or choose another.", "P0"),
    ReasonCode("LINE_ITEM_UNMATCHED", _H, 6, _F,
               "Line '{desc}' does not match any line on {po}.",
               "Map the line manually or query the vendor.", "P0"),
    ReasonCode("UNIT_PRICE_VARIANCE", _H, 6, _F,
               "{item}: ₹{actual} vs PO ₹{expected} (+{pct}%, allowed 1%).",
               "Approve variance with buyer, or request a credit note.", "P0"),
    ReasonCode("QUANTITY_EXCEEDS_PO", _H, 6, _F,
               "{item}: {prev} approved + {this} invoiced exceeds PO qty {po_qty} by {excess}.",
               "Query vendor or raise a PO amendment.", "P0"),
    ReasonCode("PO_BALANCE_EXCEEDED", _H, 6, _F,
               "Taxable value ₹{this} exceeds remaining ₹{remaining} (allowed overrun ₹{allow}) on {po}.",
               "Raise a PO amendment or dispute the invoice.", "P0"),
    ReasonCode("ARITHMETIC_MISMATCH", _H, 3, _F,
               "Equation {n} off by ₹{variance} (allowed ₹1).",
               "Contact vendor for a corrected invoice.", "P0"),
    ReasonCode("TAX_REGIME_MISMATCH", _H, 3, _F,
               "Vendor state {vs}, buyer state 29: expected {expected}, invoice shows {actual}.",
               "Request a corrected tax invoice.", "P1"),
)

REGISTRY: dict[str, ReasonCode] = {rc.code: rc for rc in _CODES}
assert len(REGISTRY) == len(_CODES), "duplicate reason code"


def get(code: str) -> ReasonCode:
    """Return the registered code; raises KeyError for any code not in PRD §7.2."""
    return REGISTRY[code]
