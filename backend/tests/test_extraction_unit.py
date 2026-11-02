"""Extraction schema, prompt and Gemini adapter behaviour, offline (fake client)."""
import json
import re
from types import SimpleNamespace

import pytest

from app.services.extraction.base import ExtractionError, Pages
from app.services.extraction.gemini import GeminiAdapter
from app.services.extraction.prompt import SYSTEM_PROMPT, text_input
from app.services.extraction.schema import InvoiceExtraction
from tests.conftest import REPO_ROOT, fixture_records

# PRD §8.4 example extraction object, verbatim.
PRD_EXAMPLE = {
    "invoice_number": {"value": "SLST/2026/0512", "confidence": 0.98},
    "invoice_date": {"value": "2026-10-28", "confidence": 0.97},
    "vendor_name": {"value": "Sri Lakshmi Steel Traders", "confidence": 0.99},
    "vendor_gstin": {"value": "29AABCS1234F1Z5", "confidence": 0.96},
    "po_reference": {"value": "PO-1042", "confidence": 0.95},
    "document_type": {"value": "tax_invoice", "confidence": 0.99},
    "currency": {"value": "INR", "confidence": 0.99},
    "line_items": [
        {"description": {"value": "TMT Steel Rod 12 mm", "confidence": 0.95},
         "item_code": {"value": None, "confidence": 0.90},
         "quantity": {"value": 80, "confidence": 0.97},
         "unit_price": {"value": 650.00, "confidence": 0.97},
         "amount": {"value": 52000.00, "confidence": 0.97}},
        {"description": {"value": "TMT Bar 16mm", "confidence": 0.96},
         "item_code": {"value": "TMT-16", "confidence": 0.93},
         "quantity": {"value": 40, "confidence": 0.97},
         "unit_price": {"value": 1200.00, "confidence": 0.97},
         "amount": {"value": 48000.00, "confidence": 0.97}},
    ],
    "subtotal": {"value": 100000.00, "confidence": 0.97},
    "discount": {"value": 5000.00, "confidence": 0.95},
    "taxable_value": {"value": 95000.00, "confidence": 0.96},
    "cgst": {"value": 8550.00, "confidence": 0.96},
    "sgst": {"value": 8550.00, "confidence": 0.96},
    "igst": {"value": None, "confidence": 0.95},
    "cgst_rate": {"value": 9, "confidence": 0.95},
    "sgst_rate": {"value": 9, "confidence": 0.95},
    "igst_rate": {"value": None, "confidence": 0.95},
    "other_charges": [{"label": "Freight", "amount": {"value": 1750.40, "confidence": 0.94}}],
    "round_off": {"value": -0.40, "confidence": 0.93},
    "total": {"value": 113850.00, "confidence": 0.98},
    "bank_account_number": {"value": "50200012341234", "confidence": 0.94},
    "ifsc": {"value": "HDFC0001234", "confidence": 0.95},
}


# ---------- schema ----------

def test_prd_example_is_schema_valid_and_round_trips():
    ex = InvoiceExtraction.model_validate(PRD_EXAMPLE)
    assert json.loads(ex.model_dump_json()) == json.loads(json.dumps(PRD_EXAMPLE))


def test_schema_covers_every_prd_field():
    expected = set(PRD_EXAMPLE)
    assert set(InvoiceExtraction.model_fields) == expected


def test_optional_fields_default_to_null():
    ex = InvoiceExtraction.model_validate({})
    for name, f in ex:
        if hasattr(f, "value"):
            assert f.value is None, name
    assert ex.line_items == [] and ex.other_charges == []


@pytest.mark.parametrize("bad", [
    {"total": {"value": 1, "confidence": 1.5}},            # confidence out of range
    {"document_type": {"value": "receipt", "confidence": 0.9}},
    {"surprise_field": {"value": 1, "confidence": 0.9}},   # extra keys rejected
    {"total": 113850.0},                                    # scalar without {value, confidence}
])
def test_schema_rejects_invalid(bad):
    with pytest.raises(Exception):
        InvoiceExtraction.model_validate(bad)


# ---------- prompt ----------

def test_prompt_states_null_and_no_compute_rules():
    p = SYSTEM_PROMPT.lower()
    for phrase in ("set its value to null", "never guess, infer", "never correct the document",
                   "return the printed total", "do not calculate"):
        assert phrase in p, phrase


def test_prompt_contains_no_fixture_or_expected_data():
    secrets_of_the_test = set()
    for v in fixture_records("vendors.json"):
        secrets_of_the_test |= {v["legal_name"], v["gstin"], v["bank_account_number"], v["ifsc"],
                                v["trusted_contact_name"]}
    for p in fixture_records("purchase_orders.json"):
        secrets_of_the_test.add(p["po_number"])
        for line in p["lines"]:
            secrets_of_the_test |= {line["item_code"], line["description"]}
    for led in fixture_records("ledger.json"):
        secrets_of_the_test.add(led["invoice_number_raw"])
    manifest = json.loads((REPO_ROOT / "invoice-templates" / "manifest.json").read_text(encoding="utf-8"))
    for inv in manifest["invoices"]:
        secrets_of_the_test |= {inv["invoice_number"], inv["total"], inv["subtotal"], inv["taxable_value"],
                                inv["round_off"], inv["discount"], inv["invoice_date"]}
        secrets_of_the_test |= {l["amount"] for l in inv["lines"]} | {l["unit_price"] for l in inv["lines"]}
        secrets_of_the_test |= {c["amount"] for c in inv["other_charges"]} | {c["label"] for c in inv["other_charges"]}
    prompt = SYSTEM_PROMPT + text_input(["PAGE"])
    leaked = sorted(s for s in secrets_of_the_test if s and s.lower() in prompt.lower())
    assert leaked == []
    assert not re.search(r"PO-\d{4}|V00\d", prompt)


# ---------- Gemini adapter with a fake client ----------

def response(text, i=100, o=50):
    return SimpleNamespace(text=text, usage_metadata=SimpleNamespace(
        prompt_token_count=i, candidates_token_count=o, thoughts_token_count=0))


class FakeModels:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def adapter(outcomes, model="gemini-3.8-flash"):
    a = GeminiAdapter("fake-key", model, retry_pause=0)
    a._client = SimpleNamespace(models=FakeModels(outcomes))
    return a


VALID = json.dumps(PRD_EXAMPLE)


def test_valid_response_first_try():
    a = adapter([response(VALID)])
    res = a.extract(Pages("text", texts=["page one"]))
    assert res.attempts == 1 and res.extraction.total.value == 113850.0
    assert (res.usage.calls, res.usage.input_tokens, res.usage.output_tokens) == (1, 100, 50)
    cfg = a._client.models.calls[0]["config"]
    assert cfg.temperature == 0
    assert cfg.response_mime_type == "application/json"
    assert cfg.system_instruction == SYSTEM_PROMPT


def test_malformed_then_valid_retries_once():
    a = adapter([response("{not json"), response(VALID)])
    res = a.extract(Pages("text", texts=["p"]))
    assert res.attempts == 2 and res.usage.calls == 2


@pytest.mark.parametrize("first, second", [
    ("{not json", '{"total": {"value": "abc", "confidence": 0.9}}'),
    (RuntimeError("503 UNAVAILABLE"), RuntimeError("504 DEADLINE_EXCEEDED")),
    ("", "{\"surprise\": 1}"),
])
def test_two_failures_raise_extraction_error(first, second):
    a = adapter([first if isinstance(first, Exception) else response(first),
                 second if isinstance(second, Exception) else response(second)])
    with pytest.raises(ExtractionError, match="after 2 attempts"):
        a.extract(Pages("text", texts=["p"]))
    assert len(a._client.models.calls) == 2


def test_vision_pages_sent_as_images():
    a = adapter([response(VALID)])
    a.extract(Pages("vision", images=[b"\x89PNG fake"]))
    contents = a._client.models.calls[0]["contents"]
    assert "images" in contents[0]
    assert contents[1].inline_data.mime_type == "image/png"
