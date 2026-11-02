"""Bank-detail masking (PRD FR-041, NFR-07, DEC-001).

Account numbers are masked to their last four digits. IFSC is a public branch
identifier and is returned unchanged (DEC-001).
"""
import re

_SEPARATORS = re.compile(r"[\s-]")


def normalise_account_number(account: str) -> str:
    """Comparison form: spaces and hyphens removed (FR-040)."""
    return _SEPARATORS.sub("", account)


def mask_account_number(account: str | None) -> str | None:
    """'918020045514471' -> 'XXXX XXXX 4471'. Never returns more than the last 4 digits."""
    if account is None:
        return None
    digits = normalise_account_number(account)
    if len(digits) <= 4:
        return "XXXX XXXX XXXX"
    return f"XXXX XXXX {digits[-4:]}"


def display_ifsc(ifsc: str | None) -> str | None:
    """IFSC is shown in full (DEC-001); returned unchanged."""
    return ifsc


def masked_bank(account: str | None, ifsc: str | None) -> str | None:
    """Combined display, e.g. 'XXXX XXXX 4471 · HDFC0005512' (FR-041)."""
    if account is None and ifsc is None:
        return None
    parts = [p for p in (mask_account_number(account), display_ifsc(ifsc)) if p]
    return " · ".join(parts)
