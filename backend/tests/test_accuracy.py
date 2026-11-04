"""Day 8 Part A: measured extraction accuracy vs decision accuracy (DEC-018). No model calls."""
import copy
import hashlib
import importlib
import json

import pytest
from fastapi.testclient import TestClient

import app.rules.vendor_bank as vendor_bank
import app.tools.build_ground_truth as gt_tool
import app.tools.measure_extraction_accuracy as m
from app.config import BACKEND_DIR
from app.main import create_app
from tests.conftest import TEST_TOKEN
from tests.test_scenarios import EXPECTED

SUFFIXES = ("pvt", "ltd", "private", "limited", "llp")
GT = json.loads(m.GROUND_TRUTH.read_text(encoding="utf-8"))
COMMITTED = json.loads(m.OUT.read_text(encoding="utf-8"))


def fv(value, confidence=0.95):
    return {"value": value, "confidence": confidence}


def tiny(expected: dict, returned: dict):
    """A one-sample ground truth and snapshot for exercising compare()."""
    fields = list(expected)
    gt = {"_meta": {"fields": fields},
          "samples": {"h1": {"test_id": "X-01", "file": "x.pdf",
                             "fields": {k: ({"expected": None, "source": "unresolved", "note": "n/a"} if v == "UNRESOLVED"
                                            else {"expected": v, "source": "test"}) for k, v in expected.items()}}}}
    snap = {"records": [{"file_hash": "h1", "payload": returned}]}
    return m.compare(gt, snap, SUFFIXES)


# ---------- comparison logic

def test_money_matches_only_in_exact_paise():
    exact = tiny({"invoice_total_paise": 11385000}, {"total": fv(113850.0)})
    assert exact["fields"][0]["correct"] == 1
    one_paisa_off = tiny({"invoice_total_paise": 11385000}, {"total": fv(113850.01)})
    assert one_paisa_off["fields"][0]["correct"] == 0
    assert one_paisa_off["fields"][0]["misses"][0] == {"test_id": "X-01", "file": "x.pdf",
                                                       "expected": 11385000, "returned": 11385001}


def test_tax_total_sums_printed_components_and_null_is_a_miss():
    assert tiny({"tax_total_paise": 1710000}, {"cgst": fv(8550.0), "sgst": fv(8550.0), "igst": fv(None)})["totals"]["correct"] == 1
    assert tiny({"tax_total_paise": 1710000}, {"cgst": fv(None), "sgst": fv(None), "igst": fv(None)})["totals"]["correct"] == 0


def test_vendor_name_uses_the_pipelines_normaliser():
    assert m.normalise_legal_name is vendor_bank.normalise_legal_name           # reused, not reimplemented
    r = tiny({"vendor_name": "Nandi IT Solutions Pvt Ltd"}, {"vendor_name": fv("Nandi IT Solutions Pvt. Ltd.")})
    assert r["totals"] == {"correct": 1, "total": 1, "unresolved": 0}
    r = tiny({"vendor_name": "Nandi IT Solutions Pvt Ltd"}, {"vendor_name": fv("Nandi Infotech Pvt Ltd")})
    assert r["totals"]["correct"] == 0


def test_other_fields_need_an_exact_match():
    r = tiny({"invoice_number": "SLST/2026/0512", "po_number": "PO-1042", "line_count": 2},
             {"invoice_number": fv("SLST-2026-0512"), "po_reference": fv("PO-1042"),
              "line_items": [{}, {}]})
    got = {row["field"]: row["correct"] for row in r["fields"]}
    assert got == {"invoice_number": 0, "po_number": 1, "line_count": 1}


def test_unresolved_fields_are_excluded_from_numerator_and_denominator():
    r = tiny({"document_type": "UNRESOLVED", "currency": "INR"}, {"document_type": fv("other"), "currency": fv("INR")})
    rows = {row["field"]: row for row in r["fields"]}
    assert (rows["document_type"]["correct"], rows["document_type"]["total"], rows["document_type"]["unresolved"]) == (0, 0, 1)
    assert rows["document_type"]["misses"] == []
    assert r["totals"] == {"correct": 1, "total": 1, "unresolved": 1}


def test_compare_is_deterministic():
    snap = json.loads(m.SNAPSHOT.read_text(encoding="utf-8"))
    a = m.compare(GT, snap, SUFFIXES)
    b = m.compare(copy.deepcopy(GT), copy.deepcopy(snap), SUFFIXES)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ---------- the committed files are current and reproducible

def test_ground_truth_is_reproducible_and_keyed_by_sample_hash():
    assert gt_tool.build() == GT                                              # regenerates identically
    hashes = {hashlib.sha256(p.read_bytes()).hexdigest() for p in (BACKEND_DIR / "samples").glob("*.pdf")}
    assert set(GT["samples"]) == hashes
    cache = {r["file_hash"] for r in json.loads(m.SNAPSHOT.read_text(encoding="utf-8"))["records"]}
    assert set(GT["samples"]) == cache                                        # the extraction cache's key


def test_ground_truth_expected_decisions_match_the_scenario_tests():
    want = [(f"T-{s[1:]}", st, sorted(c)) for s, st, c, _ in EXPECTED]
    got = [(d["test_id"], d["status"], sorted(d["reason_codes"])) for d in GT["expected_decisions"]]
    assert got == want


def test_every_unresolved_field_says_why():
    for s in GT["samples"].values():
        for name, f in s["fields"].items():
            if f["source"] == "unresolved":
                assert f["expected"] is None and f.get("note"), (s["test_id"], name)
            else:
                assert f["source"].startswith("PDF:"), (s["test_id"], name, f["source"])


def test_measurement_is_idempotent_and_the_committed_file_is_current():
    once = json.dumps(m.measure(), indent=1, ensure_ascii=False) + "\n"
    twice = json.dumps(m.measure(), indent=1, ensure_ascii=False) + "\n"
    assert once == twice
    assert once == m.OUT.read_text(encoding="utf-8")


def test_every_absorbing_mechanism_names_a_real_function():
    for field, mech in m.MECHANISMS.items():
        assert callable(getattr(importlib.import_module(mech["module"]), mech["function"])), field
    for row in COMMITTED["field_accuracy"]:
        if row["misses"]:
            assert row["field"] in m.MECHANISMS


def test_committed_measurement_numbers_are_consistent():
    rows = COMMITTED["field_accuracy"]
    for r in rows:
        assert r["correct"] + len(r["misses"]) == r["total"]
        assert r["unresolved"] == len(r["unresolved_samples"])
        assert r["total"] + r["unresolved"] == COMMITTED["_meta"]["sample_size"]
    d = COMMITTED["decision_accuracy"]
    assert d["total"] == len(EXPECTED) and d["correct"] == sum(x["correct"] for x in d["rows"])
    assert all(x["served_from_cache"] for x in d["rows"])
    assert COMMITTED["_meta"]["model_calls_made"] == 0


# ---------- endpoint

SCHEMA = {"_meta": dict, "models": dict, "field_accuracy": list, "field_totals": dict,
          "decision_accuracy": dict, "absorbed": list, "fallback_chain": dict}


def check_schema(body: dict):
    for key, kind in SCHEMA.items():
        assert isinstance(body[key], kind), key
    for key in ("policy_version", "sample_size", "measured_at", "dataset", "model_calls_made"):
        assert key in body["_meta"]
    for row in body["field_accuracy"]:
        assert {"field", "correct", "total", "unresolved", "misses"} <= set(row)
    for row in body["decision_accuracy"]["rows"]:
        assert {"test_id", "expected_status", "expected_codes", "actual_status", "actual_codes", "correct"} <= set(row)


@pytest.fixture
def api(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def test_accuracy_endpoint_serves_the_committed_file(api):
    r = api.get("/api/accuracy")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    assert r.content == m.OUT.read_bytes()                                     # as-is, no computation
    check_schema(r.json())


def test_accuracy_endpoint_works_after_a_demo_reset(api):
    reset = api.post("/api/demo/reset", headers={"X-Demo-Reset-Token": TEST_TOKEN},
                     json={"confirm": "RESET", "requester_label": "accuracy test"})
    assert reset.status_code == 200, reset.text
    r = api.get("/api/accuracy")
    assert r.status_code == 200
    check_schema(r.json())


def test_readme_headline_matches_the_committed_measurement():
    readme = (BACKEND_DIR.parent / "README.md").read_text(encoding="utf-8")
    t, d = COMMITTED["field_totals"], COMMITTED["decision_accuracy"]
    assert f"**{t['correct']} of {t['total']}** checked fields exact" in readme
    assert f"**{d['correct']} of {d['total']}** correct" in readme
    assert f"{t['unresolved']} fields the PDFs do not establish" in readme
