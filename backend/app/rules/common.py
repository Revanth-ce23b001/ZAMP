"""Small pure helpers shared by the stages: money in paise, display formatting, policy access."""
from decimal import ROUND_HALF_UP, Decimal

from app.policy import Policy
from app.utils.money import format_inr, rupees_to_paise


def paise(value) -> int | None:
    """Extracted rupee number -> paise (None stays None)."""
    return None if value is None else rupees_to_paise(value)


def mul_paise(quantity, unit_price_paise: int) -> int:
    """quantity x unit price, rounded half-up to the paisa (quantities may be fractional)."""
    return int((Decimal(str(quantity)) * unit_price_paise).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def pct_of(amount_paise: int, rate_pct) -> int:
    """amount x rate% rounded half-up to the paisa (E3)."""
    return int((Decimal(amount_paise) * Decimal(str(rate_pct)) / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def inr(p: int | None) -> str | None:
    """Display without the symbol, e.g. 1,00,000 or 1,750.40 (templates add the ₹)."""
    if p is None:
        return None
    s = format_inr(p).replace("₹", "")
    return s[:-3] if s.endswith(".00") else s


def rs(p: int | None) -> str | None:
    """Display with the symbol, e.g. ₹1,13,850 (evidence values)."""
    return None if p is None else ("-₹" + inr(-p) if p < 0 else "₹" + inr(p))


def qty(q) -> str:
    q = float(q)
    return str(int(q)) if q.is_integer() else f"{q:g}"


class Rules:
    """Typed access to the policy values the rules use. Every threshold comes from here."""

    def __init__(self, policy: Policy):
        a = policy.assumption
        other = policy.data.get("other_assumptions", {})
        p1 = policy.data.get("p1_rules", {})
        self.currency = a("A-01")
        self.buyer_state = a("A-03")
        self.allowance = a("A-08")                                # paise per equation
        self.unit_price_pct = a("A-10")                           # % over PO price
        self.qty_overrun = a("A-11")                              # units
        self.value_pct = a("A-12")["pct_of_po_total"]
        self.value_cap = a("A-12")["cap_paise"]
        self.dup_window_days = a("A-15")["window_days"]
        self.dup_total_allowance = a("A-15")["total_allowance_paise"]
        self.revision_suffixes = tuple(a("A-16"))
        self.confidence = a("A-17")
        self.date_rule = a("A-25")
        self.scoring = a("A-26")
        self.name_suffixes = tuple(other.get("vendor_name_suffixes_removed", {}).get("value", ()))
        self.dup_scope = tuple(other.get("duplicate_ledger_scope", {}).get("value", ("APPROVE", "HOLD")))
        self.rule_unsupported_document = bool(p1.get("unsupported_document", False))
        self.rule_tax_regime = bool(p1.get("tax_regime", False))

    def allowed_overrun(self, po_total: int) -> int:
        """min(pct% of PO total, cap), once per PO (FR-068, A-12)."""
        return min(po_total * self.value_pct // 100, self.value_cap)

    def price_ok(self, invoice_price: int, po_price: int) -> bool:
        """invoice <= PO x (1 + pct/100), exact in integers (FR-066)."""
        return invoice_price * 100 <= po_price * (100 + self.unit_price_pct)
