"""Stage 4 Vendor & Bank (FR-030..FR-045, OD-4, DEC-001). Pure.

Identification: exact GSTIN, else normalised legal name; a name match whose printed GSTIN
differs from that vendor's GSTIN is VENDOR_NOT_FOUND (OD-4). Bank details are compared in full
(account and IFSC) against the vendor master; evidence carries the masked account number and
the full IFSC only. Labels use the FR-034 vocabulary; nothing claims external verification.
"""
import re
from dataclasses import dataclass

from app.rules.common import Rules
from app.rules.types import (
    FAILED, NEEDS_REVIEW, NOT_CHECKED, PASSED, VENDOR_BANK, Evidence, EvidenceLog, Hit, StageOutcome, Vendor,
)
from app.services.extraction.schema import InvoiceExtraction
from app.utils.masking import masked_bank, normalise_account_number
from app.utils.normalise import normalise_gstin

GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")   # FR-031
# FR-034 permitted labels
FORMAT_VALID, FORMAT_INVALID = "Format valid", "Format invalid"
MATCHES, DOES_NOT_MATCH = "Matches vendor master", "Does not match vendor master"


def gstin_format_valid(gstin: str) -> bool:
    return bool(GSTIN_RE.match(gstin)) and 1 <= int(gstin[:2]) <= 38


def normalise_legal_name(name: str, suffixes: tuple[str, ...]) -> str:
    """FR-030: lowercase, punctuation removed, suffix words removed. No fuzzy matching."""
    words = re.sub(r"[^\w\s]", " ", name.lower()).split()
    return " ".join(w for w in words if w not in suffixes)


@dataclass
class VendorResult:
    outcome: StageOutcome
    vendor: Vendor | None


def vendor_bank(x: InvoiceExtraction, vendors: tuple[Vendor, ...], rules: Rules, log: EvidenceLog,
                unsupported: bool = False) -> VendorResult:
    out = StageOutcome(VENDOR_BANK, PASSED, "")
    if unsupported:
        out.status, out.not_checked_reason = NOT_CHECKED, "Unsupported document"
        out.summary = "Not checked: unsupported document"
        return VendorResult(out, None)

    def ev(**kw) -> int:
        i = log.add(Evidence(stage=VENDOR_BANK, **kw))
        out.evidence_ids.append(i)
        return i

    gstin = normalise_gstin(x.vendor_gstin.value) if x.vendor_gstin.value else None
    name = x.vendor_name.value

    # GSTIN format (FR-031): displayed only; never a reason code by itself.
    if gstin:
        valid = gstin_format_valid(gstin)
        ev(check="gstin_format", source_fields=["vendor_gstin"], expected="15-character GSTIN, state 01-38",
           actual=gstin, variance=None, allowed=None, result=PASSED if valid else "INFO",
           reason_code=None)
        if not valid:
            log.items[-1].variance = FORMAT_INVALID
        else:
            log.items[-1].variance = FORMAT_VALID

    # Identification (FR-030, OD-4)
    vendor = next((v for v in vendors if gstin and v.gstin == gstin), None)
    method = "GSTIN" if vendor else None
    name_match = None
    if vendor is None and name:
        key = normalise_legal_name(name, rules.name_suffixes)
        name_match = next((v for v in vendors if normalise_legal_name(v.legal_name, rules.name_suffixes) == key), None)
        if name_match and gstin and gstin != name_match.gstin:
            i = ev(check="vendor_identified", source_fields=["vendor_name", "vendor_gstin"],
                   expected=f"{name_match.vendor_id} GSTIN {name_match.gstin}", actual=f"{name} / {gstin}",
                   variance=f"Name matches {name_match.vendor_id}, GSTIN does not", allowed="exact GSTIN match",
                   result=NEEDS_REVIEW, reason_code="VENDOR_NOT_FOUND")
            out.hits.append(Hit("VENDOR_NOT_FOUND", {"gstin": gstin, "name": name}, i))
        elif name_match:
            vendor, method = name_match, "normalised legal name"
    if vendor is None and not out.hits:
        i = ev(check="vendor_identified", source_fields=["vendor_gstin", "vendor_name"],
               expected="a vendor in the master", actual=f"GSTIN {gstin or '-'} / name {name or '-'}",
               variance="no match", allowed="exact GSTIN or normalised legal name",
               result=NEEDS_REVIEW, reason_code="VENDOR_NOT_FOUND")
        out.hits.append(Hit("VENDOR_NOT_FOUND", {"gstin": gstin or "-", "name": name or "-"}, i))

    if vendor is not None:
        ev(check="vendor_identified", source_fields=["vendor_gstin" if method == "GSTIN" else "vendor_name"],
           expected=vendor.vendor_id, actual=f"{vendor.vendor_id} {vendor.legal_name}", variance=None,
           allowed=f"identified by {method}", result=PASSED, reason_code=None)
        if gstin:
            same = gstin == vendor.gstin
            ev(check="gstin_matches_master", source_fields=["vendor_gstin"], expected=vendor.gstin, actual=gstin,
               variance=MATCHES if same else DOES_NOT_MATCH, allowed="exact match",
               result=PASSED if same else "INFO", reason_code=None)
        # Status (FR-033)
        blocked = vendor.status == "blocked"
        i = ev(check="vendor_status", source_fields=["vendor master"], expected="active", actual=vendor.status,
               variance=None, allowed=None, result=FAILED if blocked else PASSED,
               reason_code="VENDOR_BLOCKED" if blocked else None)
        if blocked:
            out.hits.append(Hit("VENDOR_BLOCKED", {"name": vendor.legal_name}, i))

    # Bank (FR-040..FR-043): full account + IFSC; masked in evidence
    acct = normalise_account_number(x.bank_account_number.value) if x.bank_account_number.value else None
    ifsc = x.ifsc.value.strip().upper() if x.ifsc.value else None
    if vendor is None:
        ev(check="bank_details_match", source_fields=["bank_account_number", "ifsc"], expected=None,
           actual=masked_bank(acct, ifsc), variance=None, allowed=None, result=NOT_CHECKED, reason_code=None)
        bank_note = "bank not checked (vendor not identified)"
    elif acct is None and ifsc is None:
        ev(check="bank_details_match", source_fields=["bank_account_number", "ifsc"],
           expected=masked_bank(vendor.bank_account_number, vendor.ifsc), actual=None,
           variance="No bank details on invoice", allowed=None, result=NOT_CHECKED, reason_code=None)
        bank_note = "no bank details on invoice"
    else:
        diffs = []
        if acct is not None and acct != vendor.bank_account_number:
            diffs.append("account number differs")
        if ifsc is not None and ifsc != vendor.ifsc.upper():
            diffs.append("IFSC differs")
        missing = [f for f, v in (("account number", acct), ("IFSC", ifsc)) if v is None]
        variance = ", ".join(diffs) or None
        if missing:
            variance = ", ".join(filter(None, [variance, f"{' and '.join(missing)} not on invoice"]))
        masked_inv, masked_master = masked_bank(acct, ifsc), masked_bank(vendor.bank_account_number, vendor.ifsc)
        i = ev(check="bank_details_match", source_fields=["bank_account_number", "ifsc"], expected=masked_master,
               actual=masked_inv, variance=variance or MATCHES, allowed="exact match",
               result=NEEDS_REVIEW if diffs else PASSED, reason_code="BANK_DETAILS_MISMATCH" if diffs else None)
        if diffs:
            out.hits.append(Hit("BANK_DETAILS_MISMATCH", {
                "masked_inv": masked_inv, "masked_master": masked_master,
                "contact": f"{vendor.trusted_contact_name}, {vendor.trusted_contact_phone} (vendor master)",
            }, i))
        bank_note = "bank details differ from vendor master" if diffs else "bank details match vendor master"

    codes = {h.code for h in out.hits}
    out.status = FAILED if "VENDOR_BLOCKED" in codes else (NEEDS_REVIEW if codes else PASSED)
    if vendor is None:
        out.summary = "Vendor not identified"
    else:
        out.summary = f"{vendor.vendor_id} {vendor.legal_name} ({vendor.status}); {bank_note}"
    return VendorResult(out, vendor)
