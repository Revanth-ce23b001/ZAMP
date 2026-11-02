import hashlib

import pytest

from app.policy import ASSUMPTION_IDS, POLICY_PATH, PolicyError, load_policy


def test_policy_loads_with_every_assumption():
    policy = load_policy()
    assumptions = policy.data["assumptions"]
    assert list(ASSUMPTION_IDS) == [f"A-{i:02d}" for i in range(1, 36)]
    assert set(assumptions) == set(ASSUMPTION_IDS)
    for a_id, entry in assumptions.items():
        assert entry["label"] == "[PA]", a_id
        assert entry["unit"], a_id
        assert entry["meaning"], a_id


def test_policy_version_is_sha256_of_file():
    policy = load_policy()
    assert policy.version == hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest()
    assert len(policy.version) == 64


def test_policy_version_changes_with_content(tmp_path):
    copy = tmp_path / "policy.yaml"
    text = POLICY_PATH.read_text(encoding="utf-8")
    copy.write_text(text.replace("value: 1\n    lower_limit", "value: 30\n    lower_limit"), encoding="utf-8")
    changed = load_policy(copy)
    assert changed.assumption("A-10") == 30
    assert changed.version != load_policy().version


def test_key_thresholds_match_prd():
    p = load_policy()
    assert p.assumption("A-03") == "29"
    assert p.assumption("A-08") == 100                      # Rs 1 in paise
    assert p.assumption("A-10") == 1
    assert p.assumption("A-12") == {"pct_of_po_total": 1, "cap_paise": 100000, "applied": "once_per_po"}
    assert p.assumption("A-15")["window_days"] == 7
    assert p.assumption("A-16") == ["R", "REV", "A", "B", "V2"]
    assert p.assumption("A-17") == 0.85
    assert p.assumption("A-23") == {"accepted_type": "application/pdf", "max_bytes": 10485760, "max_pages": 5}
    assert p.assumption("A-24") == 50
    assert p.assumption("A-26")["candidate_threshold"] == 50
    assert p.assumption("A-29") == 10
    assert p.assumption("A-33") == {"success_min_interval_seconds": 30, "rejected_attempts_per_ip": 5,
                                    "rejected_window_seconds": 900}
    assert p.assumption("A-34") == "RESET"
    assert p.assumption("A-35") == "2026-10-29"


def test_missing_assumption_is_rejected(tmp_path):
    copy = tmp_path / "policy.yaml"
    text = POLICY_PATH.read_text(encoding="utf-8")
    copy.write_text(text.replace("  A-35:", "  A-99:"), encoding="utf-8")
    with pytest.raises(PolicyError):
        load_policy(copy)


def test_policy_contains_no_secret_like_keys():
    def keys(o):
        if isinstance(o, dict):
            for k, v in o.items():
                yield str(k).lower()
                yield from keys(v)
        elif isinstance(o, list):
            for v in o:
                yield from keys(v)

    for k in keys(load_policy().data):
        assert not any(w in k for w in ("api_key", "token", "password", "secret")), k
