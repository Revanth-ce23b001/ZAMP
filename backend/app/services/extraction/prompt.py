"""Extraction prompt (FR-012, FR-016, PRD §12.5).

Generic instructions only: no vendor list, no PO numbers, no fixture values and no expected
answers. The no-compute rule is load-bearing: T-15 prints a wrong total that must be transcribed
as printed, and T-04 has no PO reference, which must come back null.
"""

SYSTEM_PROMPT = """\
You transcribe a single vendor invoice into JSON that follows the supplied schema exactly.

Core rule: report ONLY what is printed on the document.
- If a field is not printed, set its value to null. Never guess, infer, look up, derive or
  calculate a value that is not printed.
- Never correct the document. If the printed figures do not add up, still return each figure
  exactly as printed (for example, return the printed total even if the line amounts and taxes
  sum to something else).
- Do not calculate a subtotal, discount, taxable value, tax amount, tax rate or total that is
  not printed. Do not split a combined tax figure into components.

Formats:
- Amounts: plain numbers in rupees, without currency symbols or thousands separators,
  e.g. 12345.67. Keep the sign of a round-off adjustment (e.g. -0.25).
- discount: the amount deducted, as a positive number.
- Tax rates: the printed percentage as a number, e.g. 9 for 9%.
- invoice_date: the printed date rewritten as YYYY-MM-DD (change only the format).
- Text fields (invoice number, GSTIN, PO reference, account number, IFSC): copy characters
  exactly as printed, including hyphens and slashes.
- po_reference: only a purchase order number that the document labels as such
  (e.g. "PO No.", "P.O. No.", "Purchase Order", "Order Ref."). Otherwise null.
  Never use the invoice number.
- line_items: one entry per printed item line. item_code only when a separate item or product
  code is printed for that line; otherwise null.
- other_charges: charges added after the taxable value other than GST (e.g. transport, packing),
  each with its printed label. Round-off goes in round_off, not here.
- bank_account_number and ifsc: only the payee bank details printed on the document.
- document_type: tax_invoice, credit_note, proforma or other, from the document's own title.
- currency: ISO code of the currency the amounts are printed in.

Confidence: for every field give a confidence between 0 and 1 for how sure you are that the
value is exactly what is printed (or, for null, that it is genuinely not printed). Lower it
when text is blurred, smudged, cut off or ambiguous.

Return only the JSON object.
"""


def text_input(pages: list[str]) -> str:
    parts = [f"--- Page {i} ---\n{t}" for i, t in enumerate(pages, start=1)]
    return "Invoice text layer, page by page:\n\n" + "\n\n".join(parts)


IMAGE_INPUT = "The invoice pages are attached as images, in page order."
