"""Day 10 (DEC-021): resolution drafts for HOLD and REJECT decisions.

Every draft is generated from the stored decision by deterministic string templates. These tests
run the real pipeline over the shipped extraction cache (no provider calls), then assert the draft
text against the evidence each decision recorded.
"""
import ast
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import BACKEND_DIR, ChainEntry, Settings
from app.db.connection import connect
from app.drafts.fields import DraftError, Facts
from app.drafts.generate import build, order_codes, render
from app.drafts.templates import (
    BUYER_INTERNAL, EQUATION_PHRASE, RECIPIENTS, TEMPLATES, TRUSTED_CONTACT, TRUSTED_CONTACT_UI_NOTE, VENDOR,
)
from app.main import create_app
from app.policy import load_policy
from app.rules.reason_codes import REGISTRY
from tests.conftest import fixture_records
from tests.test_scenarios import EXPECTED, FILES, SNAPSHOT, NoProvider

DRAFTS_DIR = BACKEND_DIR / "app" / "drafts"
POLICY = load_policy()

# The routing item 1 of the Day 10 brief specifies, asserted rather than assumed.
ROUTING = {
    "UNIT_PRICE_VARIANCE": VENDOR, "QUANTITY_EXCEEDS_PO": VENDOR, "PO_BALANCE_EXCEEDED": VENDOR,
    "ARITHMETIC_MISMATCH": VENDOR, "TAX_REGIME_MISMATCH": VENDOR, "POSSIBLE_REVISION": VENDOR,
    "POSSIBLE_DUPLICATE": VENDOR, "LINE_ITEM_UNMATCHED": VENDOR, "MISSING_REQUIRED_FIELD": VENDOR,
    "CONFIRMED_DUPLICATE": VENDOR,
    "PO_NOT_FOUND": BUYER_INTERNAL, "PO_INFERRED": BUYER_INTERNAL, "VENDOR_NOT_FOUND": BUYER_INTERNAL,
    "VENDOR_BLOCKED": BUYER_INTERNAL, "LOW_EXTRACTION_CONFIDENCE": BUYER_INTERNAL,
    "UNSUPPORTED_DOCUMENT": BUYER_INTERNAL,
    "BANK_DETAILS_MISMATCH": TRUSTED_CONTACT,
}


# ======================================================================= the pipeline, once

@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    """Every sample through the real pipeline in the PRD order, with its drafts from the API."""
    tmp = tmp_path_factory.mktemp("drafts")
    chain = (ChainEntry("gemini", "cache-only"),)
    settings = Settings(database_path=tmp / "d.db", uploads_dir=tmp / "u", frontend_origin="http://x",
                        demo_reset_token="t", gemini_api_key="k", text_chain=chain, vision_chain=chain,
                        cache_snapshot=SNAPSHOT)
    out = {}
    with TestClient(create_app(settings, adapter_for=lambda e: NoProvider())) as c:
        for sample, *_ in EXPECTED:
            r = c.post(f"/api/samples/{FILES[sample]}/process")
            assert r.status_code == 201, r.text
            assert c.app.state.worker.wait_idle(60)
            run_id = r.json()["run_id"]
            res = c.get(f"/api/runs/{run_id}/drafts")
            assert res.status_code == 200, res.text
            out[sample] = {"run_id": run_id, "drafts": res.json(), "detail": c.get(f"/api/runs/{run_id}").json(),
                           "raw": res.text}
        out["_settings"] = settings
    return out


def draft(runs, sample, recipient):
    return next(d for d in runs[sample]["drafts"] if d["recipient_type"] == recipient)


def bodies(runs) -> list[str]:
    return [d["subject"] + "\n" + d["body"]
            for s in runs if not s.startswith("_") for d in runs[s]["drafts"]]


# ======================================================================= templates and routing

def test_every_code_that_can_hold_or_reject_has_exactly_one_template():
    assert sorted(TEMPLATES) == sorted(REGISTRY)
    assert all(t.code == code for code, t in TEMPLATES.items())
    assert {t.recipient for t in TEMPLATES.values()} <= set(RECIPIENTS)


def test_recipient_routing_is_the_specified_routing():
    assert {code: t.recipient for code, t in TEMPLATES.items()} == ROUTING


def test_no_template_contains_a_money_figure_or_contact_detail():
    """Figures come from evidence; a template must not print one, nor any contact detail."""
    import re
    text = (DRAFTS_DIR / "templates.py").read_text(encoding="utf-8")
    bodies_and_subjects = "\n".join(t.subject + t.body + t.ask for t in TEMPLATES.values())
    assert "₹" not in bodies_and_subjects
    assert re.search(r"\d", bodies_and_subjects) is None, "a template carries a digit"
    assert "@" not in bodies_and_subjects
    assert "+91" not in text
    for vendor in fixture_records("vendors.json"):
        for secret in (vendor["trusted_contact_name"], vendor["trusted_contact_phone"],
                       vendor["trusted_contact_email"], vendor["bank_account_number"]):
            assert secret not in text


def test_every_arithmetic_equation_the_rules_can_fail_has_a_plain_phrase():
    import re
    validate = (BACKEND_DIR / "app" / "rules" / "validate.py").read_text(encoding="utf-8")
    names = re.findall(r'equation\(f?"([^"]+)"', validate)
    assert names
    for n in names:
        if "{comp" in n:
            assert all(f"arithmetic_{n.replace('{comp.upper()}', c)}" in EQUATION_PHRASE
                       for c in ("CGST", "SGST", "IGST")), n
        elif "{n}" in n:
            assert n == "line {n}", n            # covered by LINE_EQUATION
        else:
            assert f"arithmetic_{n}" in EQUATION_PHRASE, n


# ======================================================================= per-template fill tests

CASES = [
    # sample, recipient, fragments every draft of that code must contain (real Step 0 figures)
    ("T02", VENDOR, ["Invoice INV-SL-0489: value exceeds the balance on PO-1041",
                     "PO-1041 is for ₹3,00,000.",
                     "Invoices totalling ₹2,20,000 have already been approved against it",
                     "leaves ₹80,000 of the order unused",
                     "The taxable value of this invoice is ₹1,00,000",
                     "would be ₹3,20,000, against a ceiling of ₹3,01,000",
                     "plus an allowed overrun of ₹1,000",
                     "That is ₹19,000 above the ceiling.",
                     "100 units invoiced, 220 units already approved against order line PO-1041-1, "
                     "which covers 300 units",
                     "That is 320 units in total, 20 units more than the order allows"]),
    ("T03", TRUSTED_CONTACT, ["Invoice CPPL/7781: confirmation needed on bank details",
                              "To: Verified contact on the vendor master",
                              "On our vendor master: XXXX XXXX 4471 · HDFC0005512",
                              "On this invoice:      XXXX XXXX 4471 · ICIC0005678",
                              "What differs: IFSC differs.",
                              "last four digits only"]),
    ("T04", BUYER_INTERNAL, ["Invoice NIT/1123: confirm the purchase order",
                             "Deterministic scoring suggests PO-1031.",
                             "PO-1031 (score 100, plausible), PO-1036 (score 70)",
                             "never approved until a person confirms it"]),
    ("T05", VENDOR, ["Invoice INV-2041: duplicate of INV-2041, not payable",
                     "carries invoice number INV-2041, which we have already approved for payment",
                     "dated 12 Oct 2026 for ₹1,06,200",
                     "this copy will not be paid",
                     "This invoice has been rejected and will not be paid."]),
    ("T07", VENDOR, ["does it replace INV-2041?",
                     "revised version of INV-2041 dated 12 Oct 2026 for ₹1,06,200"]),
    ("T08", VENDOR, ["unit price differs from purchase order PO-1045",
                     "Line 1 (Copper cable 2.5 sqmm coil, order item CBL-25): invoiced at ₹1,250 per unit "
                     "against the order price of ₹1,000 per unit, +25.00% against a tolerance of +1%."]),
    ("T09", BUYER_INTERNAL, ["vendor is not in the vendor master",
                             "The invoice shows GSTIN - / name Horizon Traders.",
                             "The matching rule is exact GSTIN or normalised legal name",
                             "the result was: no match"]),
    ("T12", VENDOR, ["lines not on purchase order PO-1041", "Line 2: Cutting charges."]),
    ("T15", VENDOR, ["printed amounts do not reconcile",
                     "On the printed total against the taxable value plus taxes and charges: we compute "
                     "₹7,670, the invoice shows ₹8,670, a difference of ₹1,000 (we allow ±₹1)."]),
    ("T16", VENDOR, ["possible duplicate of INV-GC-0921",
                     "same total as INV-GC-0921 dated 3 Oct 2026 for ₹59,000, the two are 3 days apart, "
                     "and both carry the same lines"]),
    ("T17", BUYER_INTERNAL, ["PO-1031 (score 100, plausible), PO-1036 (score 100, plausible)"]),
    ("T18", BUYER_INTERNAL, ["vendor V006 is blocked, invoice rejected",
                             "from Malnad Logistics (V006) for ₹17,700 was rejected",
                             "status as blocked, where processing requires active"]),
]


@pytest.mark.parametrize("sample, recipient, fragments", CASES, ids=[c[0] for c in CASES])
def test_draft_text_matches_the_stored_evidence(runs, sample, recipient, fragments):
    d = draft(runs, sample, recipient)
    text = d["subject"] + "\n" + d["body"]
    for fragment in fragments:
        assert fragment in text, fragment


def test_t02_shows_the_remaining_balance_apart_from_the_allowed_overrun(runs):
    """FR-068: ₹80,000 remaining and ₹1,000 of overrun, never the ₹81,000 they would sum to."""
    text = draft(runs, "T02", VENDOR)["body"]
    assert "₹80,000" in text and "₹1,000" in text
    assert "81,000" not in text


def test_every_draft_covers_the_decision_codes_and_nothing_else(runs):
    for sample, status, codes, _ in EXPECTED:
        drafts = runs[sample]["drafts"]
        covered = sorted(c for d in drafts for c in d["codes_covered"])
        assert covered == sorted(codes), sample
        if status == "APPROVE":
            assert drafts == []


def test_one_draft_per_recipient_in_a_fixed_order(runs):
    for sample, *_ in EXPECTED:
        kinds = [d["recipient_type"] for d in runs[sample]["drafts"]]
        assert len(kinds) == len(set(kinds)), sample
        assert kinds == [r for r in RECIPIENTS if r in kinds], sample


def test_no_draft_prints_a_blank_a_none_or_an_unfilled_placeholder(runs):
    for text in bodies(runs):
        assert "None" not in text
        assert "{" not in text and "}" not in text
        assert ": \n" not in text


def test_drafts_are_byte_identical_when_generated_twice(runs):
    settings = runs["_settings"]
    conn = connect(settings.database_path)
    try:
        from app.drafts import drafts_for_run
        for sample, *_ in EXPECTED:
            run_id = runs[sample]["run_id"]
            first = json.dumps(drafts_for_run(conn, run_id, POLICY), ensure_ascii=False, sort_keys=True)
            second = json.dumps(drafts_for_run(conn, run_id, POLICY), ensure_ascii=False, sort_keys=True)
            assert first == second, sample
            assert first == json.dumps(runs[sample]["drafts"], ensure_ascii=False, sort_keys=True), sample
    finally:
        conn.close()


# ======================================================================= ordering and grouping

def _facts(codes_evidence: list[dict], **over) -> Facts:
    base = dict(run_display="R-0001", file_name="x.pdf", status="HOLD",
                reason_codes=[e["reason_code"] for e in codes_evidence], evidence=codes_evidence,
                buyer_state="29", invoice_number="INV-1", invoice_date="2026-10-30", total_paise=11800000,
                taxable_paise=10000000, vendor_id="V001", vendor_name="Sri Lakshmi Steel Traders",
                vendor_state_code="29", vendor_state_name="Karnataka")
    return Facts(**(base | over))


def _ev(code, check, **kw):
    row = {"check": check, "stage": "X", "source_fields": ["f"], "expected": None, "actual": None,
           "variance": None, "allowed": None, "result": "FAILED", "reason_code": code}
    return row | kw


PO_DETAIL = {"po_number": "PO-1041", "method": "explicit", "po_total": 30000000, "previously_approved": 22000000,
             "this_invoice": 10000000, "remaining": 8000000, "allowed_overrun": 100000, "ceiling": 30100000,
             "invoices_previously_approved": 2, "lines": []}


def test_multi_code_decision_produces_one_draft_per_recipient_with_the_worst_code_leading():
    f = _facts([
        _ev("PO_BALANCE_EXCEEDED", "cumulative_po_value"),
        _ev("BANK_DETAILS_MISMATCH", "bank_details_match", expected="XXXX XXXX 1234 · HDFC0001234",
            actual="XXXX XXXX 9999 · HDFC0009999", variance="account number differs"),
        _ev("VENDOR_BLOCKED", "vendor_status", expected="active", actual="blocked"),
        _ev("PO_INFERRED", "po_inferred", variance="suggested PO-1041 (score 100)"),
    ], status="REJECT", po_detail=PO_DETAIL,
        po_candidates=[{"po_number": "PO-1041", "score": 100, "plausible": True}])
    drafts = build(f)
    assert [d["recipient_type"] for d in drafts] == [VENDOR, BUYER_INTERNAL, TRUSTED_CONTACT]
    internal = next(d for d in drafts if d["recipient_type"] == BUYER_INTERNAL)
    # VENDOR_BLOCKED is the only REJECT-severity code, so it leads its draft; PO_INFERRED follows.
    assert internal["codes_covered"] == ["VENDOR_BLOCKED", "PO_INFERRED"]
    assert internal["subject"].endswith("vendor V001 is blocked, invoice rejected")
    assert "Also on this invoice:" in internal["body"]
    assert "1. " in internal["body"]


def test_code_order_is_severity_then_stage_then_name():
    assert order_codes(["PO_BALANCE_EXCEEDED", "QUANTITY_EXCEEDS_PO"]) == \
        ["PO_BALANCE_EXCEEDED", "QUANTITY_EXCEEDS_PO"]          # same severity and stage: by name
    assert order_codes(["PO_INFERRED", "ARITHMETIC_MISMATCH"]) == \
        ["ARITHMETIC_MISMATCH", "PO_INFERRED"]                  # stage 3 before stage 6
    assert order_codes(["PO_BALANCE_EXCEEDED", "CONFIRMED_DUPLICATE"]) == \
        ["CONFIRMED_DUPLICATE", "PO_BALANCE_EXCEEDED"]          # REJECT severity first


# ======================================================================= the raise, never a blank

def test_render_raises_rather_than_print_a_blank_or_none():
    for values in ({}, {"x": None}, {"x": ""}, {"x": "   "}):
        with pytest.raises(DraftError) as exc:
            render("value: {x}", values, "where")
        assert exc.value.code == "draft_placeholder_missing"
        assert "x" in exc.value.message
    assert render("value: {x}", {"x": "0"}, "where") == "value: 0"


def test_missing_evidence_value_raises_instead_of_filling_a_blank():
    # The same code with its evidence value removed: the draft must not render at all.
    good = _facts([_ev("BANK_DETAILS_MISMATCH", "bank_details_match", expected="XXXX XXXX 1234 · HDFC0001234",
                       actual="XXXX XXXX 9999 · HDFC0009999", variance="account number differs")])
    assert build(good)[0]["recipient_type"] == TRUSTED_CONTACT
    bad = _facts([_ev("BANK_DETAILS_MISMATCH", "bank_details_match", expected=None,
                      actual="XXXX XXXX 9999 · HDFC0009999", variance="account number differs")])
    with pytest.raises(DraftError) as exc:
        build(bad)
    assert exc.value.code == "draft_placeholder_missing"
    assert "master_bank" in exc.value.message


def test_a_missing_po_figure_raises_rather_than_guess():
    f = _facts([_ev("PO_BALANCE_EXCEEDED", "cumulative_po_value")],
               po_detail=PO_DETAIL | {"remaining": None})
    with pytest.raises(DraftError):
        build(f)


@pytest.mark.parametrize("code", ["UNIT_PRICE_VARIANCE", "TAX_REGIME_MISMATCH", "ARITHMETIC_MISMATCH",
                                  "LINE_ITEM_UNMATCHED", "LOW_EXTRACTION_CONFIDENCE", "QUANTITY_EXCEEDS_PO"])
def test_no_evidence_row_for_a_code_raises(code):
    f = _facts([_ev("PO_INFERRED", "po_inferred")])
    f.reason_codes = [code]
    with pytest.raises(DraftError) as exc:
        build(f)
    assert exc.value.code == "draft_evidence_missing"


# ======================================================================= the five codes no sample raises

@pytest.fixture(scope="module")
def engine_cases():
    """Real engine evidence for the codes the sample set does not raise (T-13, T-14, T-19, T-20, T-21)."""
    from tests.test_rules import TODAY, V3, f as fld, invoice, line, policy_with, run
    return {
        "MISSING_REQUIRED_FIELD": run(invoice(invoice_number=fld(None))),
        "LOW_EXTRACTION_CONFIDENCE": run(invoice(total=fld(7670.0, c=0.70))),
        "LOW_EXTRACTION_CONFIDENCE/date": run(invoice(invoice_date=fld("2026-11-04")), today=TODAY),
        "PO_NOT_FOUND/reference": run(invoice(po_reference=fld("PO-1044"))),
        "PO_NOT_FOUND/no-candidate": run(invoice(po_reference=fld(None), line_items=[line("Mystery item", 1, 1.0)],
                                                 subtotal=fld(1.0), taxable_value=fld(1.0), cgst=fld(0.09),
                                                 sgst=fld(0.09), total=fld(1.18))),
        "UNSUPPORTED_DOCUMENT": run(invoice(document_type=fld("credit_note")),
                                    policy=policy_with(**{"p1_rules.unsupported_document": True})),
        "TAX_REGIME_MISMATCH": run(invoice(vendor_name=fld(V3.legal_name), vendor_gstin=fld(V3.gstin),
                                           po_reference=fld("PO-1044"),
                                           line_items=[line("5-ply box", 100, 42.0, "CB-5PLY")],
                                           subtotal=fld(4200.0), taxable_value=fld(4200.0), cgst=fld(378.0),
                                           sgst=fld(378.0), total=fld(4956.0), bank_account_number=fld(None),
                                           ifsc=fld(None)),
                                   policy=policy_with(**{"p1_rules.tax_regime": True})),
    }


def _from_engine(result, **over) -> Facts:
    return Facts(run_display="R-0001", file_name="x.pdf", status=result.status,
                 reason_codes=result.reason_codes, evidence=result.evidence, buyer_state="29",
                 invoice_number="INV-T-1", invoice_date="2026-10-30", total_paise=767000,
                 taxable_paise=650000, vendor_id=result.vendor_id or "V001",
                 vendor_name="Sri Lakshmi Steel Traders", vendor_state_code="29", vendor_state_name="Karnataka",
                 po_detail=result.po_detail,
                 po_candidates=[{"po_number": c["po_number"], "score": c["score"], "plausible": c["plausible"]}
                                for c in result.po_candidates],
                 **over)


UNSAMPLED = [
    ("MISSING_REQUIRED_FIELD", VENDOR, ["required details are missing", "Not shown: invoice number."]),
    ("LOW_EXTRACTION_CONFIDENCE", BUYER_INTERNAL,
     ["a field needs checking against the PDF",
      "Our reader returned total with confidence 0.70 against a threshold of >= 0.85",
      "does not allow extracted fields to be edited"]),
    ("LOW_EXTRACTION_CONFIDENCE/date", BUYER_INTERNAL,
     ["The field invoice_date reads 2026-11-04, which is in the future."]),
    ("PO_NOT_FOUND/reference", BUYER_INTERNAL,
     ["no purchase order matched", "prints the reference PO-1044, which is not a purchase order of V001",
      "Closest candidates by deterministic score: PO-1042 (score 100, plausible)."]),
    ("PO_NOT_FOUND/no-candidate", BUYER_INTERNAL,
     ["prints no purchase order reference, and no open purchase order of V001 met the matching threshold "
      "(a candidate PO scoring >= 50)",
      "No open purchase order of this vendor scored high enough to suggest."]),
    ("UNSUPPORTED_DOCUMENT", BUYER_INTERNAL,
     ["not a supported document for automatic processing",
      "It reads as credit_note / INR, where the supported combination is tax_invoice / INR."]),
    ("TAX_REGIME_MISMATCH", VENDOR,
     ["tax type needs correcting", "charges CGST + SGST.",
      "is registered in Karnataka (29) and the buyer is registered in state 29",
      "should be taxed as IGST"]),
]


@pytest.mark.parametrize("key, recipient, fragments", UNSAMPLED, ids=[c[0] for c in UNSAMPLED])
def test_codes_outside_the_sample_set_render_from_real_engine_evidence(engine_cases, key, recipient, fragments):
    drafts = build(_from_engine(engine_cases[key]))
    d = next(x for x in drafts if x["recipient_type"] == recipient)
    text = d["subject"] + "\n" + d["body"]
    for fragment in fragments:
        assert fragment in text, fragment
    assert "None" not in text and "{" not in text


# ======================================================================= bank mismatch: fraud safety

def test_bank_draft_hides_the_trusted_contact_the_invoice_contact_and_the_full_account(runs):
    vendors = fixture_records("vendors.json")
    secrets = [v[k] for v in vendors for k in ("trusted_contact_name", "trusted_contact_phone",
                                               "trusted_contact_email", "bank_account_number")]
    secrets.append("50100045671234")                       # T-25(a)'s invoice account (DEC-012)
    for sample in ("T03", "T25a"):
        payload = runs[sample]["raw"]
        assert "BANK_DETAILS_MISMATCH" in payload
        for secret in secrets:
            assert secret not in payload, (sample, secret)
        # The run detail does expose the contact (FR-044); the drafts response must not.
        contact = runs[sample]["detail"]["trusted_contact"]
        assert contact["name"] and contact["phone"]
        assert contact["name"] not in payload and contact["phone"] not in payload
        body = draft(runs, sample, TRUSTED_CONTACT)["body"]
        assert body.startswith("To: Verified contact on the vendor master")
        assert "Do not send bank details to any address or number printed on the invoice" in body


def test_only_the_addressee_line_uses_the_word_verified():
    text = "\n".join(t.subject + t.body + t.ask for t in TEMPLATES.values())
    text += "\n".join(TEMPLATES[c].body for c in TEMPLATES)
    lowered = text.lower()
    assert lowered.count("verified") == lowered.count("verified contact on the vendor master")


def test_no_rendered_draft_makes_a_forbidden_claim(runs):
    """The label scan over output, not source (tests/test_draft_labels.py covers the templates)."""
    from tests.test_draft_labels import FORBIDDEN, stray_verified
    import re
    for text in bodies(runs):
        assert [label for label in FORBIDDEN if re.search(label, text, re.I)] == []
        assert stray_verified(text) == []


def test_the_ui_note_is_exact():
    assert TRUSTED_CONTACT_UI_NOTE == "Send only through the contact on file. Do not reply to the invoice."


# ======================================================================= endpoint behaviour

def test_approve_returns_an_empty_list(runs):
    for sample in ("T01", "T06", "T25b"):
        assert runs[sample]["drafts"] == []
        assert runs[sample]["detail"]["decision"]["status"] == "APPROVE"


def test_undecided_run_returns_409_and_unknown_run_404(settings):
    with TestClient(create_app(settings, adapter_for=lambda e: NoProvider())) as c:
        conn = connect(settings.database_path)
        try:
            conn.execute("INSERT INTO runs (run_id, run_number, file_name, file_hash, file_path, status, "
                         "ingest_method, created_at) VALUES "
                         "('R-pending', 9001, 'x.pdf', 'h', 'x', 'QUEUED', 'text', '2026-11-09T00:00:00+05:30')")
            conn.commit()
        finally:
            conn.close()
        r = c.get("/api/runs/R-pending/drafts")
        assert r.status_code == 409, r.text
        assert r.json()["error"] == "not_decided"
        assert "QUEUED" in r.json()["message"]
        assert c.get("/api/runs/R-nope/drafts").status_code == 404


def test_the_endpoint_writes_nothing(runs):
    """Read-only (item 5): no row, no audit event, no override."""
    settings = runs["_settings"]
    run_id = runs["T02"]["run_id"]
    tables = ("runs", "decisions", "invoice_ledger", "audit_events", "overrides", "po_consumption",
              "extractions", "demo_resets")
    conn = connect(settings.database_path)
    try:
        before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    finally:
        conn.close()
    with TestClient(create_app(settings, adapter_for=lambda e: NoProvider())) as c:
        assert c.get(f"/api/runs/{run_id}/drafts").status_code == 200
    conn = connect(settings.database_path)
    try:
        after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    finally:
        conn.close()
    assert before == after


def test_response_shape_is_exactly_the_specified_keys(runs):
    for sample, *_ in EXPECTED:
        for d in runs[sample]["drafts"]:
            assert sorted(d) == ["body", "codes_covered", "recipient_type", "subject"]
            assert d["recipient_type"] in RECIPIENTS
            assert d["subject"] and d["body"] and d["codes_covered"]


# ======================================================================= no model call, no network

BANNED_IMPORTS = {"httpx", "requests", "urllib", "urllib3", "http", "socket", "smtplib", "email", "ssl",
                  "ftplib", "telnetlib", "asyncio", "aiohttp", "google", "anthropic", "openai", "boto3",
                  "webbrowser", "subprocess"}


def _imported_roots(path: Path) -> set[str]:
    roots = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
            if node.module.startswith("app."):
                roots.add(node.module)
    return roots


def test_the_drafts_package_imports_no_http_smtp_or_model_client():
    files = sorted(DRAFTS_DIR.glob("*.py"))
    assert len(files) >= 4
    for f in files:
        roots = _imported_roots(f)
        assert not (roots & BANNED_IMPORTS), (f.name, sorted(roots & BANNED_IMPORTS))
        extraction = {r for r in roots if r.startswith("app.services.extraction")}
        assert not extraction, (f.name, extraction)


def test_the_drafts_package_source_names_no_provider_and_no_send():
    for f in sorted(DRAFTS_DIR.glob("*.py")):
        text = f.read_text(encoding="utf-8").lower()
        for word in ("gemini", "mailto:", "sendmail", "smtp", "requests.post", "httpx."):
            assert word not in text, (f.name, word)


# ======================================================================= after human review

@pytest.fixture
def reviewable(tmp_path):
    """One client on its own database, for the review flows that change a decision."""
    chain = (ChainEntry("gemini", "cache-only"),)
    settings = Settings(database_path=tmp_path / "r.db", uploads_dir=tmp_path / "u", frontend_origin="http://x",
                        demo_reset_token="t", gemini_api_key="k", text_chain=chain, vision_chain=chain,
                        cache_snapshot=SNAPSHOT)
    with TestClient(create_app(settings, adapter_for=lambda e: NoProvider())) as c:
        def start(sample: str) -> str:
            r = c.post(f"/api/samples/{FILES[sample]}/process")
            assert r.status_code == 201, r.text
            assert c.app.state.worker.wait_idle(60)
            return r.json()["run_id"]
        yield c, start


def test_drafts_follow_the_decision_after_an_override(reviewable):
    """A reviewed decision changes the drafts, because they are generated, not stored (item 3)."""
    c, start = reviewable
    run_id = start("T03")
    assert [d["recipient_type"] for d in c.get(f"/api/runs/{run_id}/drafts").json()] == [TRUSTED_CONTACT]
    r = c.post(f"/api/runs/{run_id}/override", json={
        "action": "approve", "reviewer_name": "Ashrith",
        "reason": "Vendor confirmed the account through the contact on file; letter kept.",
        "codes_overridden": ["BANK_DETAILS_MISMATCH"],
        "bank_verification": {"verified_with": "Meena Raghavan", "channel": "phone on record",
                              "outcome": "verified"}})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "APPROVE"
    assert c.get(f"/api/runs/{run_id}/drafts").json() == []       # nothing left to resolve


def test_a_reviewer_rejection_draft_says_the_invoice_will_not_be_paid(reviewable):
    c, start = reviewable
    run_id = start("T08")
    assert "on hold" in c.get(f"/api/runs/{run_id}/drafts").json()[0]["body"]
    r = c.post(f"/api/runs/{run_id}/override", json={
        "action": "reject", "reviewer_name": "Ashrith",
        "reason": "Price increase was never agreed; asked the vendor for a credit note."})
    assert r.status_code == 200 and r.json()["status"] == "REJECT"
    body = c.get(f"/api/runs/{run_id}/drafts").json()[0]["body"]
    assert "This invoice has been rejected and will not be paid." in body
    assert "on hold" not in body


def test_confirming_a_po_regenerates_the_drafts_from_the_fresh_evidence(reviewable):
    c, start = reviewable
    run_id = start("T04")
    assert c.get(f"/api/runs/{run_id}/drafts").json()[0]["codes_covered"] == ["PO_INFERRED"]
    r = c.post(f"/api/runs/{run_id}/confirm-po", json={
        "po_number": "PO-1031", "reviewer_name": "Ashrith",
        "reason": "Requester confirmed the order covers these laptops and docks."})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "APPROVE"
    assert c.get(f"/api/runs/{run_id}/drafts").json() == []
