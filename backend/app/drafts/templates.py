"""Resolution-draft templates: one per reason code that can produce HOLD or REJECT (Day 10, DEC-021).

Deterministic string templates. There is no model call anywhere in this package: the same stored
decision always renders a byte-identical draft. Every figure in a draft comes from the evidence the
rules recorded; no template contains a figure, a threshold, a person's name, an email address or a
phone number. Placeholders are filled by app.drafts.fields and rendered by app.drafts.generate,
which raises rather than print a blank, a "None" or a guessed value.

Nothing here sends anything. The trusted contact is never named: the one bank-details template is
addressed to "Verified contact on the vendor master" (FR-044, DEC-008).
"""
from dataclasses import dataclass

# Recipient types. One draft per recipient per decision.
VENDOR, BUYER_INTERNAL, TRUSTED_CONTACT = "VENDOR", "BUYER_INTERNAL", "TRUSTED_CONTACT"
RECIPIENTS = (VENDOR, BUYER_INTERNAL, TRUSTED_CONTACT)

# The addressee line of each draft. No person, address, number or invented party.
TO_LINE = {
    VENDOR: "{vendor_name}",
    BUYER_INTERNAL: "Accounts payable team (internal)",
    TRUSTED_CONTACT: "Verified contact on the vendor master",
}

# Closing line, by decision status.
CLOSING = {
    "HOLD": "Payment of this invoice is on hold until this is resolved. Our reference: {run_display}.",
    "REJECT": "This invoice has been rejected and will not be paid. Our reference: {run_display}.",
}

SIGN_OFF = "- Draft prepared by ClearMatch AP from the recorded decision. Not sent."

# The note the bank-details draft carries in the UI (item 4).
TRUSTED_CONTACT_UI_NOTE = "Send only through the contact on file. Do not reply to the invoice."


@dataclass(frozen=True)
class Template:
    """One reason code's draft: who it goes to, its subject, its body and the ask."""
    code: str
    recipient: str
    subject: str
    body: str
    ask: str


def reflow(text: str) -> str:
    """Collapse the source's hard wrapping so a filled paragraph is one line and the reader's own
    client wraps it. A paragraph with an indented line (the bank comparison block) is left alone."""
    out = []
    for para in text.strip().split("\n\n"):
        lines = para.split("\n")
        out.append(para if any(line[:1].isspace() for line in lines) else " ".join(para.split()))
    return "\n\n".join(out)


def _t(code: str, recipient: str, subject: str, body: str, ask: str) -> Template:
    return Template(code, recipient, " ".join(subject.split()), reflow(body), " ".join(ask.split()))


# --------------------------------------------------------------------- clause maps
# Where one reason code can be raised by more than one check, the shape-specific wording is a
# clause here, keyed by the check id the rules recorded. A key the generator cannot find raises;
# it never prints a blank.

DUPLICATE_CLAUSE = {
    # POSSIBLE_DUPLICATE from the same-number tier when the original is itself unresolved (OD-3).
    "duplicate_confirmed": "It carries the same invoice number as {orig_no} dated {orig_date} for "
                           "{orig_total}, which is already with us and still unresolved.",
    # POSSIBLE_DUPLICATE from the same-total, same-lines, within-the-window tier.
    "duplicate_possible": "It has the same total as {orig_no} dated {orig_date} for {orig_total}, the two "
                          "are {days} days apart, and both carry the same lines.",
}

CONFIDENCE_CLAUSE = {
    "critical_field_confidence": "Our reader returned {field} with confidence {observed} against a threshold "
                                 "of {threshold}, so the value it read may be wrong.",
    "invoice_number_corroboration": "The field {field} read as {observed}, which does not survive "
                                    "normalisation, so it cannot be compared against the ledger.",
    "invoice_date_plausible": "The field {field} reads {observed}, which is {problem}. The rule is: {threshold}.",
}

PO_LOOKUP_CLAUSE = {
    "reference_not_this_vendor": "The invoice prints the reference {reference}, which is not a purchase order "
                                 "of {vendor_id}.",
    "no_reference_no_candidate": "The invoice prints no purchase order reference, and no open purchase order "
                                 "of {vendor_id} met the matching threshold ({threshold}).",
}

# Plain-language name of each arithmetic equation the rules can fail (app/rules/validate.py).
# Per-line equations ("arithmetic_line {n}") are named by LINE_EQUATION.
EQUATION_PHRASE = {
    "arithmetic_E1": "the line amounts against the printed subtotal",
    "arithmetic_E2": "the subtotal less discount against the printed taxable value",
    "arithmetic_E3 CGST": "CGST against the taxable value at the printed CGST rate",
    "arithmetic_E3 SGST": "SGST against the taxable value at the printed SGST rate",
    "arithmetic_E3 IGST": "IGST against the taxable value at the printed IGST rate",
    "arithmetic_E4": "the printed total against the taxable value plus taxes and charges",
}
LINE_EQUATION = "line {n}: quantity times unit price against the printed line amount"


# --------------------------------------------------------------------- the templates

TEMPLATES: dict[str, Template] = {t.code: t for t in (

    # ---- PO match, vendor-facing
    _t("PO_BALANCE_EXCEEDED", VENDOR,
       "Invoice {invoice_number}: value exceeds the balance on {po_number}",
       """
{document_ref} cannot be passed for payment against purchase order {po_number}.

{po_number} is for {po_total}. Invoices totalling {previously_approved} have already been approved
against it, which leaves {remaining} of the order unused. The taxable value of this invoice is
{this_invoice}, so the cumulative value against {po_number} would be {cumulative}, against a ceiling
of {ceiling}: the order total of {po_total} plus an allowed overrun of {allowed_overrun}. That is
{excess} above the ceiling.
""",
       """Please confirm whether an amendment to {po_number} is in progress, and send us its reference. If
       no amendment is coming, please issue a revised invoice for no more than {remaining}, the value still
       unused on {po_number}."""),

    _t("QUANTITY_EXCEEDS_PO", VENDOR,
       "Invoice {invoice_number}: quantity exceeds purchase order {po_number}",
       """
{document_ref} bills more against purchase order {po_number} than the order covers.

{items}
""",
       """Please confirm whether an amendment to {po_number} raises these quantities, or issue a revised
       invoice for no more than the quantity still open on each order line."""),

    _t("UNIT_PRICE_VARIANCE", VENDOR,
       "Invoice {invoice_number}: unit price differs from purchase order {po_number}",
       """
{document_ref} is priced above purchase order {po_number}.

{items}
""",
       """Please either issue a revised invoice at the order price, or send us the buyer's written approval
       for the new price on {po_number}."""),

    _t("LINE_ITEM_UNMATCHED", VENDOR,
       "Invoice {invoice_number}: lines not on purchase order {po_number}",
       """
{document_ref} contains charges we cannot match to any line of purchase order {po_number}.

{items}
""",
       """Please tell us which line of {po_number} each of these charges belongs to, or issue a revised
       invoice covering only what {po_number} orders."""),

    # ---- Validate, vendor-facing
    _t("ARITHMETIC_MISMATCH", VENDOR,
       "Invoice {invoice_number}: printed amounts do not reconcile",
       """
The amounts printed on {document_ref} do not add up.

{items}
""",
       """Please check these amounts and send a corrected invoice. We cannot pass an invoice whose printed
       figures do not reconcile."""),

    _t("TAX_REGIME_MISMATCH", VENDOR,
       "Invoice {invoice_number}: tax type needs correcting",
       """
{document_ref} charges {actual}.

{vendor_name} is registered in {vendor_state} and the buyer is registered in state {buyer_state}, so
this supply should be taxed as {expected}.
""",
       "Please issue a corrected tax invoice applying {expected}."),

    _t("MISSING_REQUIRED_FIELD", VENDOR,
       "{subject_ref}: required details are missing",
       """
{document_ref} cannot be processed because details we need are not shown on it.

Not shown: {missing_fields}.
""",
       """Please send a corrected invoice showing these details. We cannot process the invoice without
       them."""),

    # ---- Duplicates, vendor-facing
    _t("CONFIRMED_DUPLICATE", VENDOR,
       "Invoice {invoice_number}: duplicate of {orig_no}, not payable",
       """
{document_ref} carries invoice number {orig_no}, which we have already approved for payment. Our
record of {orig_no} is dated {orig_date} for {orig_total}; this invoice is for {total}.

Because the original was already approved, this copy will not be paid and nothing has been recorded
against a purchase order.
""",
       """If this is not a copy of {orig_no}, please reissue it under a new invoice number and tell us what
       it covers. If it was sent in error, no action is needed."""),

    _t("POSSIBLE_DUPLICATE", VENDOR,
       "Invoice {invoice_number}: possible duplicate of {orig_no}",
       """
{document_ref} for {total} may be a second copy of an invoice we already hold.

{match_clause}
""",
       """Please confirm whether {invoice_number} is a separate invoice to be paid in addition to {orig_no},
       or a copy of it. We are holding payment until you confirm."""),

    _t("POSSIBLE_REVISION", VENDOR,
       "Invoice {invoice_number}: does it replace {orig_no}?",
       """
{document_ref} appears to be a revised version of {orig_no} dated {orig_date} for {orig_total}, which
we already hold.
""",
       """Please confirm whether {invoice_number} replaces {orig_no} in full, or is a separate invoice to be
       paid in addition to it."""),

    # ---- Vendor and bank, internal
    _t("VENDOR_BLOCKED", BUYER_INTERNAL,
       "{subject_ref}: vendor {vendor_id} is blocked, invoice rejected",
       """
{document_ref} from {vendor_name} ({vendor_id}) for {total} was rejected.

The vendor master records this vendor's status as {actual}, where processing requires {expected}.
Nothing has been consumed against any purchase order.
""",
       """Confirm with procurement whether the block on {vendor_id} still applies. If it has been lifted,
       update the vendor master and resubmit the invoice. If it stands, return the invoice to the vendor."""),

    _t("VENDOR_NOT_FOUND", BUYER_INTERNAL,
       "{subject_ref}: vendor is not in the vendor master",
       """
{document_ref} for {total} cannot be matched to a vendor in the vendor master, so the duplicate and
purchase-order checks did not run.

The invoice shows {actual}. The matching rule is {matching_rule}, and the result was: {result}.
""",
       """Route this to vendor onboarding: either add the vendor to the master with its GSTIN, or confirm
       which existing vendor this invoice belongs to and have the invoice resubmitted."""),

    # ---- Validate, internal
    _t("LOW_EXTRACTION_CONFIDENCE", BUYER_INTERNAL,
       "{subject_ref}: a field needs checking against the PDF",
       """
{document_ref} is on hold because a field cannot be relied on as read.

{confidence_clause}
""",
       """Open the PDF and check that field. This prototype does not allow extracted fields to be edited: if
       the reading is wrong, reject the invoice and ask the vendor for a clean copy."""),

    _t("UNSUPPORTED_DOCUMENT", BUYER_INTERNAL,
       "{subject_ref}: not a supported document for automatic processing",
       """
{document_ref} is outside what this prototype processes automatically, so the vendor, duplicate and
purchase-order checks did not run.

It reads as {actual}, where the supported combination is {expected}.
""",
       """Process this document by hand. Nothing has been recorded against a vendor or a purchase order."""),

    # ---- PO match, internal
    _t("PO_NOT_FOUND", BUYER_INTERNAL,
       "{subject_ref}: no purchase order matched",
       """
{document_ref} from {vendor_name} ({vendor_id}) for {total} has not been matched to a purchase order,
so the line, price, quantity and balance checks did not run.

{lookup_clause}

{candidate_clause}
""",
       """Find the purchase order and confirm it on the decision screen, or ask the requester which order
       this invoice belongs to."""),

    _t("PO_INFERRED", BUYER_INTERNAL,
       "{subject_ref}: confirm the purchase order",
       """
{document_ref} from {vendor_name} ({vendor_id}) for {total} prints no purchase order reference.
Deterministic scoring suggests {po_number}.

Candidates considered: {candidate_clause}

Line, price, quantity and balance checks were run against {po_number} as evidence only. Nothing has
been consumed against it: an inferred purchase order is never approved until a person confirms it.
""",
       """Confirm {po_number} on the decision screen if it is the right order, or choose another. Do not
       approve on the suggestion alone."""),

    # ---- Bank details: the fraud-safe draft (item 4)
    _t("BANK_DETAILS_MISMATCH", TRUSTED_CONTACT,
       "Invoice {invoice_number}: confirmation needed on bank details",
       """
The bank details printed on {document_ref} from {vendor_name} ({vendor_id}) do not match the details
held for this vendor on our vendor master.

  On our vendor master: {master_bank}
  On this invoice:      {invoice_bank}

What differs: {difference}.

Account numbers are shown as their last four digits only. Payment of this invoice is on hold until the
correct account is confirmed through the contact channel already recorded on our vendor master.
""",
       """Please confirm which account is correct, using the contact channel already recorded on our vendor
       master. Do not send bank details to any address or number printed on the invoice; we will not act on
       details received that way."""),
)}
