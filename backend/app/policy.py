"""Load policy.yaml and compute its version hash (PRD FR-117, §12.1)."""
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

POLICY_PATH = Path(__file__).with_name("policy.yaml")
ASSUMPTION_IDS = tuple(f"A-{i:02d}" for i in range(1, 36))
PA_LABEL = "[PA]"
REQUIRED_ENTRY_KEYS = ("name", "value", "unit", "label", "meaning", "source")


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class Policy:
    data: dict[str, Any]
    version: str  # full SHA-256 hex of the file bytes
    path: Path

    @property
    def version_short(self) -> str:
        return self.version[:8]

    def assumption(self, assumption_id: str) -> Any:
        return self.data["assumptions"][assumption_id]["value"]

    def by_name(self, name: str) -> Any:
        for entry in self.data["assumptions"].values():
            if entry["name"] == name:
                return entry["value"]
        raise KeyError(name)


def load_policy(path: Path = POLICY_PATH) -> Policy:
    raw = path.read_bytes()
    data = yaml.safe_load(raw)
    if not isinstance(data, dict) or not isinstance(data.get("assumptions"), dict):
        raise PolicyError("policy.yaml must contain an 'assumptions' mapping")

    assumptions = data["assumptions"]
    missing = [a for a in ASSUMPTION_IDS if a not in assumptions]
    extra = [a for a in assumptions if a not in ASSUMPTION_IDS]
    if missing or extra:
        raise PolicyError(f"policy.yaml assumptions mismatch: missing={missing} extra={extra}")

    for a_id, entry in assumptions.items():
        absent = [k for k in REQUIRED_ENTRY_KEYS if k not in entry]
        if absent:
            raise PolicyError(f"{a_id} missing keys {absent}")
        if entry["label"] != PA_LABEL:
            raise PolicyError(f"{a_id} label must be {PA_LABEL}")
    for name, entry in (data.get("other_assumptions") or {}).items():
        if entry.get("label") != PA_LABEL:
            raise PolicyError(f"other_assumptions.{name} label must be {PA_LABEL}")

    return Policy(data=data, version=policy_hash(raw), path=path)


def policy_hash(raw: bytes) -> str:
    """SHA-256 of policy.yaml with line endings normalised to LF, so the same policy has the same
    version on Windows (CRLF checkout) and on the Linux deployment (LF)."""
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
