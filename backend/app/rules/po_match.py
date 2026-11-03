"""Stage 6 PO Match (FR-060..FR-069, §9.3, §9.7, OD-1, OD-2, DEC-003, DEC-014). Pure.

Explicit PO lookup or deterministic candidate inference (60/30/10), line pairing by item code
then normalised description/alias, unmatched lines, unit-price tolerance, cumulative quantity
per PO line and the once-per-PO cumulative value tolerance. Consumption comes only from
committed APPROVE decisions (passed in as ReferenceData.consumption).
"""
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.rules.common import Rules, inr, mul_paise, paise, qty, rs
from app.rules.types import (
    FAILED, NEEDS_REVIEW, NOT_CHECKED, PASSED, PO_MATCH, PO, Consumption, Evidence, EvidenceLog, Hit, POLine,
    StageOutcome,
)
from app.services.extraction.schema import InvoiceExtraction, LineItem
from app.utils.normalise import normalise_description, normalise_item_code, normalise_po_reference


@dataclass(frozen=True)
class Pair:
    index: int                 # invoice line number (1-based)
    line: LineItem
    po_line: POLine
    method: str                # "code" | "description" | "alias"


def pair_lines(lines: list[LineItem], po: PO) -> tuple[list[Pair], list[tuple[int, LineItem]]]:
    """FR-063 then FR-064; each PO line pairs with at most one invoice line."""
    used, pairs, unpaired = set(), [], []
    for n, li in enumerate(lines, start=1):
        hit = None
        if li.item_code.value:
            code = normalise_item_code(li.item_code.value)
            hit = next(((pl, "code") for pl in po.lines
                        if pl.po_line_id not in used and normalise_item_code(pl.item_code) == code), None)
        if hit is None and li.description.value:
            desc = normalise_description(li.description.value)
            hit = next(((pl, "description" if desc == normalise_description(pl.description) else "alias")
                        for pl in po.lines if pl.po_line_id not in used
                        and (desc == normalise_description(pl.description) or desc in pl.aliases)), None)
        if hit:
            used.add(hit[0].po_line_id)
            pairs.append(Pair(n, li, hit[0], hit[1]))
        else:
            unpaired.append((n, li))
    return pairs, unpaired


def line_signature(lines: list[LineItem], po: PO | None) -> tuple[tuple[str, float], ...]:
    """DEC-014: paired PO line's item code, else printed item code, else normalised description."""
    canonical = {}
    if po is not None:
        for p in pair_lines(lines, po)[0]:
            canonical[p.index] = p.po_line.item_code
    keys = []
    for n, li in enumerate(lines, start=1):
        key = canonical.get(n) or (normalise_item_code(li.item_code.value) if li.item_code.value
                                   else normalise_description(li.description.value or ""))
        keys.append((key, float(li.quantity.value or 0)))
    return tuple(sorted(keys))


def resolve_po(reference: str | None, pos: tuple[PO, ...]) -> PO | None:
    if not reference:
        return None
    norm = normalise_po_reference(reference)
    return next((p for p in pos if normalise_po_reference(p.po_number) == norm), None)


def allocate(pairs: list[Pair], taxable: int) -> list[tuple[str, float, int]]:
    """Consumption value per paired line: line amount less a pro-rata share of the document
    discount (A-28), i.e. the taxable value split by line amount. Largest-remainder rounding so
    the parts sum exactly to the taxable value. Returns [(po_line_id, quantity, value_paise)]."""
    amounts = [mul_paise(p.line.quantity.value, paise(p.line.unit_price.value)) for p in pairs]
    total = sum(amounts)
    if not pairs or total <= 0:
        return []
    raw = [(taxable * a, i) for i, a in enumerate(amounts)]
    parts = [r // total for r, _ in raw]
    leftover = taxable - sum(parts)
    for _, i in sorted(raw, key=lambda t: (-(t[0] % total), t[1]))[:leftover]:
        parts[i] += 1
    return [(p.po_line.po_line_id, float(p.line.quantity.value), parts[i]) for i, p in enumerate(pairs)]


@dataclass
class Candidate:
    po_number: str
    score: int
    breakdown: dict
    plausible: bool = False


def score_candidate(lines: list[LineItem], po: PO, taxable: int | None, invoice_date: date | None,
                    consumption: Consumption, rules: Rules) -> Candidate:
    """§9.7 / OD-1: 60 x paired fraction + 30 if value fits + 10 if within the date window."""
    s = rules.scoring
    pairs, _ = pair_lines(lines, po)
    frac = len(pairs) / len(lines) if lines else 0.0
    remaining = po.po_total - consumption.value_by_po.get(po.po_number, 0)
    fits = taxable is not None and taxable <= remaining + rules.allowed_overrun(po.po_total)
    in_window = invoice_date is not None and po.po_date <= invoice_date <= po.po_date + timedelta(days=s["date_window_days"])
    pts_lines = round(s["weight_line_pairing"] * frac)
    score = pts_lines + (s["weight_value_fits"] if fits else 0) + (s["weight_date_window"] if in_window else 0)
    return Candidate(po.po_number, score, {"line_pairing": pts_lines, "paired": f"{len(pairs)}/{len(lines)}",
                                           "value_fits": s["weight_value_fits"] if fits else 0,
                                           "date_window": s["weight_date_window"] if in_window else 0})


def candidates_for(lines, vendor_pos, taxable, invoice_date, consumption, rules) -> list[Candidate]:
    """Vendor POs with remaining value > 0, scored; only those >= threshold are shown (FR-061/062)."""
    s = rules.scoring
    scored = [score_candidate(lines, po, taxable, invoice_date, consumption, rules) for po in vendor_pos
              if po.po_total - consumption.value_by_po.get(po.po_number, 0) > 0]
    shown = sorted((c for c in scored if c.score >= s["candidate_threshold"]), key=lambda c: (-c.score, c.po_number))
    if len(shown) >= 2 and shown[0].score - shown[1].score <= s["plausible_gap_points"]:
        shown[0].plausible = shown[1].plausible = True
    elif shown:
        shown[0].plausible = True
    return shown


@dataclass
class POResult:
    outcome: StageOutcome
    matched_po: str | None = None
    method: str | None = None                  # "explicit" | "inferred" | "confirmed"
    candidates: list[Candidate] = field(default_factory=list)
    commit_lines: list[tuple[str, float, int]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def po_match(x: InvoiceExtraction, vendor_id: str | None, pos: tuple[PO, ...], consumption: Consumption,
             taxable: int | None, invoice_date: date | None, rules: Rules, log: EvidenceLog,
             unsupported: bool = False, confirmed_po: str | None = None) -> POResult:
    out = StageOutcome(PO_MATCH, PASSED, "")
    res = POResult(out)
    if unsupported or vendor_id is None:
        out.status = NOT_CHECKED
        out.not_checked_reason = "Unsupported document" if unsupported else "Vendor not identified"
        out.summary = f"Not checked: {out.not_checked_reason.lower()}"
        return res

    def ev(**kw) -> int:
        i = log.add(Evidence(stage=PO_MATCH, **kw))
        out.evidence_ids.append(i)
        return i

    lines = list(x.line_items)
    vendor_pos = tuple(p for p in pos if p.vendor_id == vendor_id)
    reference = confirmed_po or x.po_reference.value

    # ---- which PO (FR-060 explicit, FR-061/062 inferred, FR-103 confirmed)
    if reference:
        po = resolve_po(reference, pos)
        if po is None or po.vendor_id != vendor_id:
            res.candidates = candidates_for(lines, vendor_pos, taxable, invoice_date, consumption, rules)
            i = ev(check="po_lookup", source_fields=["po_reference"], expected=f"a PO of {vendor_id}",
                   actual=reference, variance=f"{reference} not found for this vendor", allowed=None,
                   result=NEEDS_REVIEW, reason_code="PO_NOT_FOUND")
            out.hits.append(Hit("PO_NOT_FOUND", {"ref": f" (reference {reference})"}, i))
            if res.candidates:
                res.notes.append("Suggested: " + ", ".join(f"{c.po_number} ({c.score})" for c in res.candidates))
            out.status, out.summary = NEEDS_REVIEW, f"{reference} not found for this vendor; line checks not run"
            return res
        method = "confirmed" if confirmed_po else "explicit"
        ev(check="po_lookup", source_fields=["po_reference"], expected=f"a PO of {vendor_id}", actual=po.po_number,
           variance=None, allowed=None, result=PASSED, reason_code=None)
        raise_codes = True
    else:
        res.candidates = candidates_for(lines, vendor_pos, taxable, invoice_date, consumption, rules)
        for c in res.candidates:
            ev(check="po_candidate_score", source_fields=["line_items", "taxable_value", "invoice_date"],
               expected=f">= {rules.scoring['candidate_threshold']}", actual=c.score, variance=c.breakdown,
               allowed=None, result="INFO", reason_code=None)
        if not res.candidates:
            i = ev(check="po_lookup", source_fields=["po_reference"], expected="a candidate PO scoring >= "
                   f"{rules.scoring['candidate_threshold']}", actual="none", variance=None, allowed=None,
                   result=NEEDS_REVIEW, reason_code="PO_NOT_FOUND")
            out.hits.append(Hit("PO_NOT_FOUND", {"ref": ""}, i))
            out.status, out.summary = NEEDS_REVIEW, "No PO reference and no candidate PO"
            return res
        best = res.candidates[0]
        i = ev(check="po_inferred", source_fields=["po_reference"], expected="explicit PO reference",
               actual=None, variance=f"suggested {best.po_number} (score {best.score})", allowed=None,
               result=NEEDS_REVIEW, reason_code="PO_INFERRED")
        out.hits.append(Hit("PO_INFERRED", {"po": best.po_number, "s": best.score}, i))
        plausible = [c for c in res.candidates if c.plausible]
        if len(plausible) >= 2:
            res.notes.append("Two plausible POs: " + ", ".join(f"{c.po_number} ({c.score})" for c in plausible) + ".")
        po, method = resolve_po(best.po_number, pos), "inferred"
        # FR-061: line checks run against the best candidate for evidence only; an inferred PO
        # is never approved, and confirming it (FR-103) re-runs this stage as explicit.
        raise_codes = False

    res.matched_po, res.method = po.po_number, method

    def flag(code: str, params: dict, i: int):
        if raise_codes:
            out.hits.append(Hit(code, params, i))

    def result(ok: bool) -> str:
        return PASSED if ok else (FAILED if raise_codes else "INFO")

    # ---- line pairing (FR-063..FR-065)
    pairs, unpaired = pair_lines(lines, po)
    for p in pairs:
        ev(check="line_pairing", source_fields=[f"line_items[{p.index}]"], expected=p.po_line.po_line_id,
           actual=p.line.description.value, variance=None, allowed=f"by {p.method}", result=PASSED, reason_code=None)
    for n, li in unpaired:
        i = ev(check="line_pairing", source_fields=[f"line_items[{n}]"], expected=f"a line of {po.po_number}",
               actual=li.description.value, variance="no matching PO line", allowed="item code, description or alias",
               result=result(False), reason_code="LINE_ITEM_UNMATCHED" if raise_codes else None)
        flag("LINE_ITEM_UNMATCHED", {"desc": li.description.value, "po": po.po_number}, i)

    # ---- unit price (FR-066)
    for p in pairs:
        inv_price, po_price = paise(p.line.unit_price.value), p.po_line.unit_price
        ok = rules.price_ok(inv_price, po_price)
        pct = (inv_price - po_price) * 100 / po_price if po_price else 0
        i = ev(check="unit_price", source_fields=[f"line_items[{p.index}].unit_price"], expected=rs(po_price),
               actual=rs(inv_price), variance=f"{pct:+.2f}%", allowed=f"+{rules.unit_price_pct}%",
               result=result(ok), reason_code=None if ok or not raise_codes else "UNIT_PRICE_VARIANCE")
        if not ok:
            flag("UNIT_PRICE_VARIANCE", {"item": p.po_line.item_code, "actual": inr(inv_price),
                                         "expected": inr(po_price), "pct": f"{pct:g}" if pct == int(pct) else f"{pct:.2f}"}, i)

    # ---- cumulative quantity per PO line (FR-067)
    for p in pairs:
        prev = consumption.qty_by_line.get(p.po_line.po_line_id, 0)
        this = float(p.line.quantity.value)
        limit = p.po_line.quantity + rules.qty_overrun
        excess = prev + this - limit
        ok = excess <= 0
        i = ev(check="cumulative_quantity", source_fields=[f"line_items[{p.index}].quantity"],
               expected=f"<= {qty(limit)}", actual=f"{qty(prev)} approved + {qty(this)} this invoice",
               variance=qty(max(excess, 0)), allowed=f"{qty(rules.qty_overrun)} over PO quantity",
               result=result(ok), reason_code=None if ok or not raise_codes else "QUANTITY_EXCEEDS_PO")
        if not ok:
            flag("QUANTITY_EXCEEDS_PO", {"item": p.po_line.item_code, "prev": qty(prev), "this": qty(this),
                                         "po_qty": qty(p.po_line.quantity), "excess": qty(excess)}, i)

    # ---- cumulative PO value, tolerance once per PO (FR-068, OD-2)
    if taxable is not None:
        committed = consumption.value_by_po.get(po.po_number, 0)
        allow = rules.allowed_overrun(po.po_total)
        remaining = po.po_total - committed
        ceiling = po.po_total + allow
        ok = committed + taxable <= ceiling
        earlier = consumption.invoices_by_po.get(po.po_number, 0)
        i = ev(check="cumulative_po_value", source_fields=["taxable_value"],
               expected=f"<= {rs(ceiling)} (PO total {rs(po.po_total)} + allowed overrun {rs(allow)})",
               actual=f"{rs(committed)} approved + {rs(taxable)} this invoice",
               variance=rs(committed + taxable - ceiling) if not ok else None,
               allowed=rs(allow), result=result(ok), reason_code=None if ok or not raise_codes else "PO_BALANCE_EXCEEDED")
        if not ok:
            flag("PO_BALANCE_EXCEEDED", {"this": inr(taxable), "remaining": inr(remaining), "allow": inr(allow),
                                         "po": po.po_number, "earlier_invoices": earlier}, i)
        elif committed + taxable > po.po_total:
            res.notes.append(f"PO tolerance used {rs(committed + taxable - po.po_total)} of {rs(allow)} on {po.po_number}.")

    if raise_codes and not unpaired:
        res.commit_lines = allocate(pairs, taxable) if taxable is not None else []

    codes = {h.code for h in out.hits}
    failed = codes & {"LINE_ITEM_UNMATCHED", "UNIT_PRICE_VARIANCE", "QUANTITY_EXCEEDS_PO", "PO_BALANCE_EXCEEDED"}
    out.status = FAILED if failed else (NEEDS_REVIEW if codes else PASSED)
    paired = f"{len(pairs)} of {len(lines)} lines paired"
    out.summary = (f"{po.po_number} ({method}), {paired}" + ("" if not codes else ": " + ", ".join(sorted(codes)))
                   if raise_codes else f"No PO on invoice; suggested {po.po_number}, {paired}")
    return res
