"""Measure per-field extraction accuracy and decision accuracy from the committed cache (DEC-018).

    python -m app.tools.measure_extraction_accuracy        (from backend/)

Reads only committed files: the extraction cache snapshot, the ground truth, policy.yaml,
render.yaml and docs/MODEL_STRATEGY.md. Makes zero model calls: the decision run uses an adapter
that raises if called. Writes backend/seed/extraction_accuracy.json. Idempotent: the only
timestamp written is the snapshot's own export time, so re-running produces an identical file.
"""
import dataclasses
import json
import re
import sys
import tempfile
from pathlib import Path

import yaml

from app.config import BACKEND_DIR
from app.policy import load_policy
from app.rules.common import paise
from app.rules.vendor_bank import normalise_legal_name

REPO = BACKEND_DIR.parent
SNAPSHOT = BACKEND_DIR / "seed" / "extraction_cache.json"
GROUND_TRUTH = BACKEND_DIR / "seed" / "extraction_ground_truth.json"
OUT = BACKEND_DIR / "seed" / "extraction_accuracy.json"
MODEL_STRATEGY = REPO / "docs" / "MODEL_STRATEGY.md"
RENDER_YAML = REPO / "render.yaml"

MONEY = {"subtotal_paise", "tax_total_paise", "invoice_total_paise"}
# Ground-truth field -> the extraction fields (and evidence source_fields) it corresponds to.
SOURCE_FIELDS = {
    "invoice_number": ["invoice_number"], "invoice_date": ["invoice_date"], "vendor_name": ["vendor_name"],
    "vendor_gstin": ["vendor_gstin"], "po_number": ["po_reference"], "currency": ["currency"],
    "document_type": ["document_type"], "subtotal_paise": ["subtotal"], "tax_total_paise": ["cgst", "sgst", "igst"],
    "invoice_total_paise": ["total"], "line_count": ["line_items"],
}
# The deterministic code path that handles a field the model got wrong. Each entry names a real
# function (tests/test_accuracy.py imports every one). A field that starts missing without an
# entry here fails the measurement, so a mechanism is never claimed without being read from code.
MECHANISMS = {
    "vendor_gstin": {
        "code_path": "app.rules.vendor_bank.vendor_bank -> app.rules.vendor_bank.normalise_legal_name",
        "module": "app.rules.vendor_bank", "function": "normalise_legal_name",
        "evidence_checks": ["vendor_identified", "gstin_format", "gstin_matches_master"],
        "mechanism": "With no GSTIN read, vendor_bank() skips the GSTIN format and master-match checks (they are "
                     "display-only and never raise a code) and identifies the vendor by exact match of the "
                     "normalised legal name (FR-030). If no master vendor has that name, it holds VENDOR_NOT_FOUND.",
    },
    "subtotal_paise": {
        "code_path": "app.rules.validate.amounts / app.rules.validate.validate",
        "module": "app.rules.validate", "function": "amounts",
        "evidence_checks": ["arithmetic_E1", "arithmetic_E2"],
        "mechanism": "With no subtotal read, amounts() uses the sum of line amounts (quantity x unit price) as the "
                     "base for the taxable value, and validate() marks E1 Not Checked ('subtotal not printed'). "
                     "Subtotal is not a critical field (FR-017), so its absence raises no code.",
    },
}


# ------------------------------------------------------------------ comparison (pure)

def returned_value(field: str, payload: dict, suffixes: tuple[str, ...]):
    """The model's value for a ground-truth field, in the ground truth's units."""
    v = lambda k: (payload.get(k) or {}).get("value")              # noqa: E731
    if field == "po_number":
        return v("po_reference")
    if field == "subtotal_paise":
        return paise(v("subtotal"))
    if field == "invoice_total_paise":
        return paise(v("total"))
    if field == "tax_total_paise":
        parts = [paise(v(k)) for k in ("cgst", "sgst", "igst")]
        parts = [p for p in parts if p is not None]
        return sum(parts) if parts else None
    if field == "line_count":
        return len(payload.get("line_items") or [])
    return v(field)


def field_correct(field: str, expected, returned, suffixes: tuple[str, ...]) -> bool:
    """Exact match; money in exact paise; vendor_name through the pipeline's own normaliser."""
    if field == "vendor_name":
        if expected is None or returned is None:
            return expected == returned
        return normalise_legal_name(str(expected), suffixes) == normalise_legal_name(str(returned), suffixes)
    return expected == returned


def compare(ground_truth: dict, snapshot: dict, suffixes: tuple[str, ...]) -> dict:
    by_hash = {r["file_hash"]: r for r in snapshot["records"]}
    fields = ground_truth["_meta"]["fields"]
    stats = {f: {"field": f, "correct": 0, "total": 0, "unresolved": 0, "unresolved_samples": [], "misses": []}
             for f in fields}
    missing_cache = []
    for file_hash, gt in sorted(ground_truth["samples"].items(), key=lambda kv: kv[1]["test_id"]):
        rec = by_hash.get(file_hash)
        if rec is None:
            missing_cache.append(gt["test_id"])
            continue
        for f in fields:
            exp = gt["fields"][f]
            s = stats[f]
            if exp["source"] == "unresolved":
                s["unresolved"] += 1
                s["unresolved_samples"].append({"test_id": gt["test_id"], "reason": exp.get("note")})
                continue
            got = returned_value(f, rec["payload"], suffixes)
            s["total"] += 1
            if field_correct(f, exp["expected"], got, suffixes):
                s["correct"] += 1
            else:
                s["misses"].append({"test_id": gt["test_id"], "file": gt["file"], "expected": exp["expected"],
                                    "returned": got})
    rows = [stats[f] for f in fields]
    return {
        "fields": rows,
        "totals": {"correct": sum(r["correct"] for r in rows), "total": sum(r["total"] for r in rows),
                   "unresolved": sum(r["unresolved"] for r in rows)},
        "samples_without_cached_extraction": missing_cache,
    }


# ------------------------------------------------------------------ decisions (real pipeline, no model)

class _NoProvider:
    provider, model = "none", "none"

    def extract(self, pages, schema=None):
        raise RuntimeError("measurement must be served from the extraction cache snapshot")


def run_decisions(ground_truth: dict) -> dict[str, dict]:
    """Process every sample through the real pipeline, in PRD §13 order, on a fresh seeded database,
    serving extractions from the committed snapshot. Returns test_id -> decision + evidence."""
    from fastapi.testclient import TestClient

    from app.config import ChainEntry, Settings
    from app.db.connection import connect
    from app.main import create_app

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        chain = (ChainEntry("gemini", "cache-only"),)
        settings = Settings(database_path=tmp / "measure.db", uploads_dir=tmp / "uploads",
                            frontend_origin="http://localhost", demo_reset_token="measure", gemini_api_key="unused",
                            text_chain=chain, vision_chain=chain, cache_snapshot=SNAPSHOT)
        out = {}
        with TestClient(create_app(settings, adapter_for=lambda e: _NoProvider())) as c:
            for exp in ground_truth["expected_decisions"]:
                r = c.post(f"/api/samples/{exp['file']}/process")
                r.raise_for_status()
                assert c.app.state.worker.wait_idle(120)
                conn = connect(settings.database_path)
                run = conn.execute("SELECT status, error_message FROM runs WHERE run_id = ?", (r.json()["run_id"],)).fetchone()
                dec = conn.execute("SELECT status, reason_codes, evidence FROM decisions WHERE run_id = ?",
                                   (r.json()["run_id"],)).fetchone()
                cached = conn.execute("SELECT cached FROM extractions WHERE run_id = ?", (r.json()["run_id"],)).fetchone()
                conn.close()
                out[exp["test_id"]] = {
                    "run_status": run[0], "error": run[1], "cached": bool(cached[0]) if cached else None,
                    "status": dec[0] if dec else None, "reason_codes": json.loads(dec[1]) if dec else [],
                    "evidence": json.loads(dec[2]) if dec else [],
                }
    return out


def decision_accuracy(ground_truth: dict, actual: dict) -> dict:
    rows = []
    for exp in ground_truth["expected_decisions"]:
        a = actual[exp["test_id"]]
        ok = a["status"] == exp["status"] and set(a["reason_codes"]) == set(exp["reason_codes"])
        rows.append({"test_id": exp["test_id"], "expected_status": exp["status"],
                     "expected_codes": sorted(exp["reason_codes"]), "actual_status": a["status"],
                     "actual_codes": sorted(a["reason_codes"]), "correct": ok, "served_from_cache": a["cached"],
                     "source": exp["source"]})
    return {
        "rule": "correct only if the status AND the full set of reason codes match",
        "source": ground_truth["_meta"]["expected_decisions_source"],
        "correct": sum(r["correct"] for r in rows), "total": len(rows),
        "rows": rows, "mismatches": [r for r in rows if not r["correct"]],
    }


def absorbed(field_rows: list[dict], actual: dict) -> list[dict]:
    """For each field with misses: the code path that handled it, and the evidence the pipeline
    actually recorded for every missed sample (the mechanism's evidence checks, plus any evidence row
    whose source_fields cite the field), ending with the sample's final decision."""
    out = []
    for row in field_rows:
        if not row["misses"]:
            continue
        if row["field"] not in MECHANISMS:
            raise SystemExit(f"{row['field']} has misses but no code-derived mechanism in MECHANISMS; read the "
                             "code path that handles it and add one before publishing a measurement.")
        m = MECHANISMS[row["field"]]
        cites, checks = set(SOURCE_FIELDS[row["field"]]), set(m["evidence_checks"])
        evidence = []
        for miss in row["misses"]:
            for e in actual[miss["test_id"]]["evidence"]:
                if e["check"] in checks or cites & set(e.get("source_fields") or []):
                    evidence.append({"test_id": miss["test_id"], "check": e["check"], "result": e["result"],
                                     "allowed": e.get("allowed"), "variance": e.get("variance"),
                                     "reason_code": e.get("reason_code")})
            evidence.append({"test_id": miss["test_id"], "check": "final decision",
                             "result": actual[miss["test_id"]]["status"],
                             "allowed": None, "variance": None,
                             "reason_code": ", ".join(actual[miss["test_id"]]["reason_codes"]) or None})
        out.append({"field": row["field"], "misses": len(row["misses"]), "code_path": m["code_path"],
                    "mechanism": m["mechanism"], "evidence": evidence})
    return out


# ------------------------------------------------------------------ provenance

def fallback_chain() -> dict:
    text = " ".join(MODEL_STRATEGY.read_text(encoding="utf-8").split())
    m = re.search(r"(re-extracting the original (\d+) samples with it reproduced (\d+) of (\d+) outcomes\. "
                  r"(T-\d+) and (T-\d+) gained a spurious `(\w+)`, which holds an invoice; it never approved one wrongly\.)",
                  text)
    if not m:
        return {"source": "docs/MODEL_STRATEGY.md", "supported": False,
                "note": "the document does not state a precise fallback figure in the expected form"}
    return {"source": "docs/MODEL_STRATEGY.md", "supported": True, "quoted_line": m.group(1),
            "fallback_model": "gemini-3.5-flash-lite", "samples_measured": int(m.group(4)),
            "outcomes_reproduced": int(m.group(3)), "samples_with_wrong_outcome": [m.group(5), m.group(6)],
            "spurious_code": m.group(7)}


def configured_chain() -> dict:
    env = {e["key"]: e.get("value") for e in yaml.safe_load(RENDER_YAML.read_text(encoding="utf-8"))["services"][0]["envVars"]}
    split = lambda s: [x.strip() for x in (s or "").split(",") if x.strip()]      # noqa: E731
    return {"source": "render.yaml (deployed configuration, DEC-017)",
            "text": split(env.get("EXTRACTION_TEXT_CHAIN")), "vision": split(env.get("EXTRACTION_VISION_CHAIN"))}


def measure() -> dict:
    policy = load_policy()
    suffixes = tuple(policy.data["other_assumptions"]["vendor_name_suffixes_removed"]["value"])
    gt = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    fields = compare(gt, snap, suffixes)
    actual = run_decisions(gt)
    models: dict[str, int] = {}
    for r in snap["records"]:
        key = f"{r['provider']}:{r['model']}"
        models[key] = models.get(key, 0) + 1
    return {
        "_meta": {
            "kind": "measured extraction and decision accuracy (Day 8, DEC-018). Generated by "
                    "python -m app.tools.measure_extraction_accuracy; never hand-edited.",
            "dataset": "synthetic demo dataset",
            "sample_size": len(gt["samples"]),
            "model_calls_made": 0,
            "measured_at": snap["_meta"]["exported_at"],
            "measured_at_meaning": "export time of the committed extraction snapshot that was measured (a fixed "
                                   "input timestamp, so re-running is byte-identical)",
            "policy_version": policy.version,
            "inputs": ["backend/seed/extraction_cache.json", "backend/seed/extraction_ground_truth.json",
                       "backend/app/policy.yaml", "render.yaml", "docs/MODEL_STRATEGY.md"],
            "match_rule": "exact match; money compared in exact integer paise; vendor_name compared after "
                          "app.rules.vendor_bank.normalise_legal_name; unresolved ground truth excluded",
        },
        "models": {"cached_extractions_by_model": dict(sorted(models.items())), "configured_chain": configured_chain()},
        "field_accuracy": fields["fields"],
        "field_totals": fields["totals"],
        "samples_without_cached_extraction": fields["samples_without_cached_extraction"],
        "decision_accuracy": decision_accuracy(gt, actual),
        "absorbed": absorbed(fields["fields"], actual),
        "fallback_chain": fallback_chain(),
    }


def main() -> int:
    import logging
    logging.disable(logging.INFO)
    OUT.write_text(json.dumps(measure(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
