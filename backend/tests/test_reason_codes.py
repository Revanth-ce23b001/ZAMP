"""The registry must match PRD §7.2 exactly. The expected table is parsed from the PRD itself."""
import re

from app.rules.reason_codes import REGISTRY, Severity, StageStatus
from tests.conftest import REPO_ROOT

PRD = REPO_ROOT / "ClearMatch_AP_PRD_v1.2_Final.md"


def prd_reason_code_table() -> dict[str, dict]:
    text = PRD.read_text(encoding="utf-8")
    section = text.split("### 7.2 Final reason-code list", 1)[1].split("### 7.3", 1)[0]
    rows = {}
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        code, severity, stage, status, template, action, priority = cells
        rows[code.strip("`")] = {
            "severity": severity,
            "stage": int(stage),
            "stage_status": status,
            "template": template.strip('"'),
            "next_action": action.strip('"'),
            "priority": re.match(r"P\d", priority).group(0),   # "P1 (rule)" -> "P1"
        }
    return rows


def test_exactly_17_codes_matching_prd_set():
    prd = prd_reason_code_table()
    assert len(prd) == 17
    assert len(REGISTRY) == 17
    assert set(REGISTRY) == set(prd)


def test_every_field_matches_prd():
    status_map = {"Failed": StageStatus.FAILED, "Needs Review": StageStatus.NEEDS_REVIEW}
    for code, row in prd_reason_code_table().items():
        rc = REGISTRY[code]
        assert rc.severity.value == row["severity"], code
        assert rc.stage == row["stage"], code
        assert rc.stage_status == status_map[row["stage_status"]], code
        assert rc.template == row["template"], code
        assert rc.next_action == row["next_action"], code
        assert rc.priority == row["priority"], code


def test_reject_severity_only_for_duplicate_and_blocked():
    rejects = {c for c, rc in REGISTRY.items() if rc.severity is Severity.REJECT}
    assert rejects == {"CONFIRMED_DUPLICATE", "VENDOR_BLOCKED"}


def test_p1_codes():
    p1 = {c for c, rc in REGISTRY.items() if rc.priority == "P1"}
    assert p1 == {"UNSUPPORTED_DOCUMENT", "TAX_REGIME_MISMATCH"}
