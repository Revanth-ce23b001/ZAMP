"""Money helpers. Storage and arithmetic are integer paise; rupees exist only at the
extraction boundary and for display (PRD §8, Day 1 brief invariant 3)."""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

PAISE_PER_RUPEE = 100


def rupees_to_paise(rupees: int | float | str | Decimal) -> int:
    """Convert a rupee amount to integer paise, rounding half away from zero.

    Floats are converted through str() so 1750.40 becomes 175040, not 175039.
    """
    if isinstance(rupees, bool):
        raise TypeError("bool is not a money amount")
    try:
        d = Decimal(str(rupees)) if isinstance(rupees, float) else Decimal(rupees)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"not a money amount: {rupees!r}") from exc
    if not d.is_finite():
        raise ValueError(f"not a money amount: {rupees!r}")
    return int((d * PAISE_PER_RUPEE).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def paise_to_rupees(paise: int) -> Decimal:
    """Exact rupee value of an integer paise amount (for display or extraction comparison)."""
    if not isinstance(paise, int) or isinstance(paise, bool):
        raise TypeError("paise must be an int")
    return Decimal(paise) / PAISE_PER_RUPEE


def _indian_group(digits: str) -> str:
    """Group an integer digit string Indian style: last 3, then pairs (1,13,850)."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    pairs = []
    while len(head) > 2:
        pairs.insert(0, head[-2:])
        head = head[:-2]
    if head:
        pairs.insert(0, head)
    return ",".join(pairs) + "," + tail


def format_inr(paise: int) -> str:
    """Format paise as rupees with Indian grouping and two decimals: 11385000 -> '₹1,13,850.00'."""
    if not isinstance(paise, int) or isinstance(paise, bool):
        raise TypeError("paise must be an int")
    sign = "-" if paise < 0 else ""
    rupees, rem = divmod(abs(paise), PAISE_PER_RUPEE)
    return f"{sign}₹{_indian_group(str(rupees))}.{rem:02d}"


def money(paise: int) -> dict:
    """API representation of an amount: integer paise plus a display string."""
    return {"paise": paise, "display": format_inr(paise)}
