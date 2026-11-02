"""Invoice extraction object (PRD §8.4, FR-012, FR-013).

Every scalar is {value, confidence}. Fields not printed on the document are null; they are
never computed. Confidence is a model-reported signal in [0, 1], not a calibrated probability.
Amounts are rupees as printed (numbers); conversion to paise happens in the rules engine.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DocumentType = Literal["tax_invoice", "credit_note", "proforma", "other"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Confidence(_Strict):
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class TextField(Confidence):
    value: str | None = None


class NumberField(Confidence):
    value: float | None = None


class DateField(Confidence):
    # YYYY-MM-DD as printed (format converted only). Parsing and plausibility are Stage 3 (FR-021).
    value: str | None = None


class DocumentTypeField(Confidence):
    value: DocumentType | None = None


class LineItem(_Strict):
    description: TextField = Field(default_factory=TextField)
    item_code: TextField = Field(default_factory=TextField)
    quantity: NumberField = Field(default_factory=NumberField)
    unit_price: NumberField = Field(default_factory=NumberField)
    amount: NumberField = Field(default_factory=NumberField)


class OtherCharge(_Strict):
    label: str
    amount: NumberField = Field(default_factory=NumberField)


class InvoiceExtraction(_Strict):
    invoice_number: TextField = Field(default_factory=TextField)
    invoice_date: DateField = Field(default_factory=DateField)
    vendor_name: TextField = Field(default_factory=TextField)
    vendor_gstin: TextField = Field(default_factory=TextField)
    po_reference: TextField = Field(default_factory=TextField)
    document_type: DocumentTypeField = Field(default_factory=DocumentTypeField)
    currency: TextField = Field(default_factory=TextField)
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: NumberField = Field(default_factory=NumberField)
    discount: NumberField = Field(default_factory=NumberField)
    taxable_value: NumberField = Field(default_factory=NumberField)
    cgst: NumberField = Field(default_factory=NumberField)
    sgst: NumberField = Field(default_factory=NumberField)
    igst: NumberField = Field(default_factory=NumberField)
    cgst_rate: NumberField = Field(default_factory=NumberField)
    sgst_rate: NumberField = Field(default_factory=NumberField)
    igst_rate: NumberField = Field(default_factory=NumberField)
    other_charges: list[OtherCharge] = Field(default_factory=list)
    round_off: NumberField = Field(default_factory=NumberField)
    total: NumberField = Field(default_factory=NumberField)
    bank_account_number: TextField = Field(default_factory=TextField)
    ifsc: TextField = Field(default_factory=TextField)

    def field_count(self) -> int:
        """Number of scalar fields with a non-null value (for the stage summary)."""
        scalars = [v for k, v in self if isinstance(v, Confidence) and v.value is not None]
        lines = sum(1 for li in self.line_items for _, f in li if f.value is not None)
        return len(scalars) + lines + len(self.other_charges)
