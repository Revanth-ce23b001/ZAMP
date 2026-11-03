"""Stage 7 Decide (FR-080..FR-085, §7.2, §7.3, §7.4). Pure.

All codes are kept. Final status: REJECT if any REJECT-severity code, else HOLD if any
HOLD-severity code, else APPROVE. Codes are ordered by severity, then stage order. Each code
renders one sentence and one next action from the registry templates with real values.
"""
from dataclasses import dataclass

from app.rules.reason_codes import REGISTRY, Severity
from app.rules.types import DECIDE, PASSED, Hit, StageOutcome


@dataclass
class Decision:
    status: str
    reason_codes: list[str]
    summary: list[str]
    next_actions: list[str]
    outcome: StageOutcome


def _render(template: str, params: dict) -> str:
    try:
        return template.format(**params)
    except (KeyError, IndexError):
        return template


def decide(stages: list[StageOutcome]) -> Decision:
    hits: list[Hit] = [h for s in stages for h in s.hits]
    for h in hits:
        if h.code not in REGISTRY:             # FR-081: only registered codes may exist
            raise ValueError(f"unregistered reason code {h.code}")
    first: dict[str, Hit] = {}
    for h in hits:
        first.setdefault(h.code, h)
    ordered = sorted(first, key=lambda c: (REGISTRY[c].severity != Severity.REJECT, REGISTRY[c].stage))
    severities = {REGISTRY[c].severity for c in ordered}
    status = "REJECT" if Severity.REJECT in severities else "HOLD" if Severity.HOLD in severities else "APPROVE"
    summary = [first[c].sentence or _render(REGISTRY[c].template, first[c].params) for c in ordered]
    actions = [_render(REGISTRY[c].next_action, first[c].params) for c in ordered]
    text = status if not ordered else f"{status}: {', '.join(ordered)}"
    return Decision(status, ordered, summary, actions, StageOutcome(DECIDE, PASSED, text))
