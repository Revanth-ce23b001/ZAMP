"""Day 9 (DEC-020): every check id the pipeline can emit has a plain-language name and a group, and the
candidate-score rows can be named by PO number from stored data."""
import json
import re
from pathlib import Path

import pytest

from app.config import BACKEND_DIR

LABELS = json.loads((BACKEND_DIR.parent / "frontend" / "lib" / "check-labels.json").read_text(encoding="utf-8"))
RULES = BACKEND_DIR / "app" / "rules"


def covered(check: str) -> bool:
    return check in LABELS["names"] or any(re.match(p["match"], check) for p in LABELS["patterns"])


def test_every_literal_check_id_in_the_rules_has_a_name():
    ids = set()
    for f in RULES.glob("*.py"):
        ids |= set(re.findall(r'check="([^"]+)"', f.read_text(encoding="utf-8")))
    assert ids, "no check ids found"
    assert sorted(i for i in ids if not covered(i)) == []


def test_formatted_check_ids_are_only_the_arithmetic_equations():
    """A new f-string check id would bypass the literal scan above; only arithmetic_{n} is allowed."""
    found = []
    for f in RULES.glob("*.py"):
        found += re.findall(r'check=f"([^"]+)"', f.read_text(encoding="utf-8"))
    assert set(found) == {"arithmetic_{n}"}
    names = re.findall(r'equation\(f?"([^"]+)"', (RULES / "validate.py").read_text(encoding="utf-8"))
    for n in names:
        variants = [n.replace("{comp.upper()}", c) for c in ("CGST", "SGST", "IGST")] if "{comp" in n else [n.replace("{n}", "1")]
        for v in variants:
            assert covered(f"arithmetic_{v}"), v


def test_every_group_has_a_stage_order():
    ordered = {g for groups in LABELS["group_order"].values() for g in groups}
    used = {v["group"] for v in LABELS["names"].values()} | {p["group"] for p in LABELS["patterns"]}
    assert used <= ordered


@pytest.fixture(scope="module")
def all_runs(tmp_path_factory):
    from fastapi.testclient import TestClient

    from app.config import ChainEntry, Settings
    from app.main import create_app
    from tests.test_scenarios import EXPECTED, FILES, SNAPSHOT, NoProvider
    tmp = tmp_path_factory.mktemp("labels")
    chain = (ChainEntry("gemini", "cache-only"),)
    s = Settings(database_path=tmp / "l.db", uploads_dir=tmp / "u", frontend_origin="http://x", demo_reset_token="t",
                 gemini_api_key="k", text_chain=chain, vision_chain=chain, cache_snapshot=SNAPSHOT)
    out = {}
    with TestClient(create_app(s, adapter_for=lambda e: NoProvider())) as c:
        for sample, *_ in EXPECTED:
            rid = c.post(f"/api/samples/{FILES[sample]}/process").json()["run_id"]
            assert c.app.state.worker.wait_idle(60)
            out[sample] = c.get(f"/api/runs/{rid}").json()
    return out


def test_every_check_id_emitted_on_the_samples_has_a_name(all_runs):
    emitted = {e["check"] for d in all_runs.values() for e in d["decision"]["evidence"]}
    assert sorted(i for i in emitted if not covered(i)) == []


def test_candidate_rows_line_up_with_the_candidate_list(all_runs):
    """The check table names each candidate-score row by the PO at the same position in po_candidates.
    po_match.py writes both from one list in one order; this pins that guarantee on every sample."""
    seen = 0
    for sample, d in all_runs.items():
        rows = [e for e in d["decision"]["evidence"] if e["check"] == "po_candidate_score"]
        cands = d["decision"]["po_candidates"]
        assert len(rows) == len(cands), sample
        for e, c in zip(rows, cands):
            assert (e["actual"], e["variance"]) == (c["score"], c["breakdown"]), sample
            seen += 1
    assert seen >= 4                                   # T-04 (2 candidates) and T-17 (2 candidates)
    t17 = [c["po_number"] for c in all_runs["T17"]["decision"]["po_candidates"]]
    assert t17 == ["PO-1031", "PO-1036"]               # two genuine candidates with identical scores
