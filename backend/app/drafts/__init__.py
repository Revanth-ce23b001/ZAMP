"""Resolution drafts for HOLD and REJECT decisions (Day 10, DEC-021).

Deterministic string templates filled from the stored decision's evidence. No model call, no
network, no persistence, and nothing is ever sent.
"""
from app.drafts.fields import DraftError
from app.drafts.generate import drafts_for_run

__all__ = ["DraftError", "drafts_for_run"]
