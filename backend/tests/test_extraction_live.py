"""Live provider tests (cost money). Skipped unless pytest is run with --run-live.

    python -m pytest tests/test_extraction_live.py --run-live -s                  # one pass
    python -m pytest tests/test_extraction_live.py --run-live -s -m determinism   # 3 passes

The cache is bypassed entirely: these tests call ingest + the configured adapter directly,
so every extraction is a live provider call. Credentials come from backend/.env.
"""
import json
import os
import time
from collections import defaultdict
from pathlib import Path

import pytest
from dotenv import dotenv_values

from app.policy import load_policy
from app.services.extraction.base import Usage
from app.services.extraction.gemini import GeminiAdapter
from app.services.ingest import ingest
from app.utils.money import rupees_to_paise
from tests.conftest import REPO_ROOT

pytestmark = pytest.mark.live

BACKEND = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((REPO_ROOT / "invoice-templates" / "manifest.json").read_text(encoding="utf-8"))
INVOICES = {i["test_id"]: i for i in MANIFEST["invoices"]}
REPORT = BACKEND / "data" / "extraction_report.json"
USAGE: dict[str, list[Usage]] = defaultdict(list)


@pytest.fixture(scope="module")
def live_adapter():
    env = dotenv_values(BACKEND / ".env")
    if env.get("LLM_PROVIDER") != "gemini" or not env.get("GEMINI_API_KEY"):
        pytest.skip("backend/.env has no Gemini configuration")
    return GeminiAdapter(env["GEMINI_API_KEY"], env["LLM_MODEL"])


# Pause between live calls to stay under the provider's per-minute request quota (a free-tier
# key allows only a few requests per minute). Override with LIVE_CALL_INTERVAL=<seconds>.
CALL_INTERVAL = float(os.environ.get("LIVE_CALL_INTERVAL", "8"))
_last_call = [0.0]


def extract_all(adapter, label: str) -> dict:
    policy = load_policy()
    min_chars = policy.assumption("A-24")
    dpi = policy.data["other_assumptions"]["render_dpi"]["value"]
    out = {}
    for tid, inv in INVOICES.items():
        pages = ingest(REPO_ROOT / MANIFEST["_meta"]["output_dir"] / inv["file"], min_chars, dpi).pages
        wait = CALL_INTERVAL - (time.monotonic() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.monotonic()
        res = adapter.extract(pages)
        USAGE[label].append(res.usage)
        out[tid] = res
    return out


@pytest.fixture(scope="module")
def pass1(live_adapter):
    return extract_all(live_adapter, "pass1")


# ---------- comparison with the manifest (printed values) ----------

def _num(v):
    return None if v is None else rupees_to_paise(v)


# Layouts that print no item-code column: the printed value there is genuinely null.
LAYOUTS_WITHOUT_ITEM_CODES = {"banded"}


def compare(inv: dict, ex) -> tuple[list[str], list[str]]:
    """Returns (wrong fields, low-confidence fields). Low confidence (< 0.85) is reported only for
    fields that are actually printed; absent fields legitimately come back null with low
    confidence and are not counted."""
    wrong, low = [], []

    def check(name, got_field, expected, numeric=False):
        got = got_field.value
        same = (_num(got) == _num(expected)) if numeric else (got == expected)
        if not same:
            wrong.append(f"{name}: got {got!r}, printed {expected!r}")
        if expected is not None and got_field.confidence < 0.85:
            low.append(f"{name} ({got_field.confidence:.2f})")

    check("invoice_number", ex.invoice_number, inv["invoice_number"])
    check("invoice_date", ex.invoice_date, inv["invoice_date"])
    check("vendor_name", ex.vendor_name, inv["vendor"]["legal_name"])
    check("vendor_gstin", ex.vendor_gstin, inv["vendor"]["gstin"])
    check("po_reference", ex.po_reference, inv["po_reference"])
    check("document_type", ex.document_type, "tax_invoice")
    check("currency", ex.currency, "INR")
    for key in ("subtotal", "discount", "taxable_value", "round_off", "total"):
        check(key, getattr(ex, key), inv[key], numeric=True)
    for comp in ("cgst", "sgst", "igst"):
        printed = inv["tax"][comp]
        check(comp, getattr(ex, comp), printed["amount"] if printed else None, numeric=True)
        check(f"{comp}_rate", getattr(ex, f"{comp}_rate"), printed["rate"] if printed else None, numeric=True)
    bank = inv.get("bank") or {}
    check("bank_account_number", ex.bank_account_number, bank.get("account_number"))
    check("ifsc", ex.ifsc, bank.get("ifsc"))
    if len(ex.line_items) != len(inv["lines"]):
        wrong.append(f"line_items: got {len(ex.line_items)}, printed {len(inv['lines'])}")
    for i, (got, want) in enumerate(zip(ex.line_items, inv["lines"]), start=1):
        check(f"line{i}.description", got.description, want["description"])
        printed_code = None if inv["layout"] in LAYOUTS_WITHOUT_ITEM_CODES else want["item_code"]
        check(f"line{i}.item_code", got.item_code, printed_code)
        for k in ("quantity", "unit_price", "amount"):
            check(f"line{i}.{k}", getattr(got, k), want[k], numeric=True)
    printed_charges = [(c["label"], _num(c["amount"])) for c in inv["other_charges"]]
    got_charges = [(c.label, _num(c.amount.value)) for c in ex.other_charges]
    if got_charges != printed_charges:
        wrong.append(f"other_charges: got {got_charges}, printed {printed_charges}")
    return wrong, low


def test_every_sample_is_schema_valid(pass1):
    assert set(pass1) == set(INVOICES)
    for tid, res in pass1.items():
        assert res.extraction.__class__.__name__ == "InvoiceExtraction", tid


def test_null_discipline(pass1):
    assert pass1["T-04"].extraction.po_reference.value is None
    assert rupees_to_paise(pass1["T-15"].extraction.total.value) == 867000      # printed, not 7,670
    assert pass1["T-06"].extraction.bank_account_number.value is None


def test_accuracy_report(pass1):
    report = {}
    print("\nTest   wrong  low-conf  details")
    for tid, res in pass1.items():
        wrong, low = compare(INVOICES[tid], res.extraction)
        report[tid] = {"wrong": wrong, "low_confidence": low, "attempts": res.attempts}
        print(f"{tid:6} {len(wrong):5}  {len(low):8}  {'; '.join(wrong + ['LOW ' + l for l in low]) or 'all fields exact'}")
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps({"pass1": report, "usage": usage_summary()}, indent=2), encoding="utf-8")


@pytest.mark.determinism
def test_determinism_three_passes(live_adapter, pass1):
    """PRD §16 Day 2 exit condition: with the cache off, three consecutive extractions of every
    sample return identical values."""
    passes = [pass1, extract_all(live_adapter, "pass2"), extract_all(live_adapter, "pass3")]
    diffs = {}
    for tid in INVOICES:
        dumps = [p[tid].extraction.model_dump(mode="json") for p in passes]
        if not (dumps[0] == dumps[1] == dumps[2]):
            diffs[tid] = _diff_fields(dumps)
    print("\nDeterminism (3 passes, cache disabled):")
    for tid in INVOICES:
        print(f"  {tid:6} {'IDENTICAL' if tid not in diffs else 'DIFFERS: ' + '; '.join(diffs[tid][:6])}")
    print(f"Usage: {usage_summary()}")
    report = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {}
    report.update({"determinism_diffs": diffs, "usage": usage_summary()})
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    assert diffs == {}


def _diff_fields(dumps: list[dict], prefix="") -> list[str]:
    out = []
    a = dumps[0]
    if isinstance(a, dict):
        for k in a:
            out += _diff_fields([d.get(k) if isinstance(d, dict) else None for d in dumps], f"{prefix}{k}.")
    elif isinstance(a, list) and all(isinstance(d, list) and len(d) == len(a) for d in dumps):
        for i in range(len(a)):
            out += _diff_fields([d[i] for d in dumps], f"{prefix}{i}.")
    elif not (dumps[0] == dumps[1] == dumps[2]):
        out.append(f"{prefix.rstrip('.')}={dumps}")
    return out


def usage_summary() -> dict:
    total = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0}
    per_pass = {}
    for label, items in USAGE.items():
        s = {k: sum(getattr(u, k) for u in items) for k in total}
        per_pass[label] = s
        for k in total:
            total[k] += s[k]
    return {"per_pass": per_pass, "total": total}
