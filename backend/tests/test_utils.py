from decimal import Decimal

import pytest

from app.utils.masking import display_ifsc, mask_account_number, masked_bank
from app.utils.money import format_inr, money, paise_to_rupees, rupees_to_paise
from app.utils.normalise import normalise_description, normalise_invoice_number
from tests.conftest import fixture_records

# ---------- money ----------

@pytest.mark.parametrize("rupees, paise", [
    (113850, 11385000), ("113850.00", 11385000), (1750.40, 175040), (-0.40, -40),
    (0.37, 37), (Decimal("656.50"), 65650), (0, 0), ("0.005", 1),
])
def test_rupees_to_paise(rupees, paise):
    assert rupees_to_paise(rupees) == paise


def test_paise_to_rupees_exact():
    assert paise_to_rupees(11385000) == Decimal("113850")
    assert paise_to_rupees(-40) == Decimal("-0.4")


@pytest.mark.parametrize("paise, text", [
    (11385000, "₹1,13,850.00"), (8000000, "₹80,000.00"), (12500000, "₹1,25,000.00"),
    (81500000, "₹8,15,000.00"), (100000000, "₹10,00,000.00"), (100000, "₹1,000.00"),
    (99, "₹0.99"), (0, "₹0.00"), (-40, "-₹0.40"), (1234567890, "₹1,23,45,678.90"),
])
def test_format_inr_indian_grouping(paise, text):
    assert format_inr(paise) == text
    assert money(paise) == {"paise": paise, "display": text}


def test_money_rejects_bad_input():
    with pytest.raises(TypeError):
        format_inr(1.5)
    with pytest.raises(ValueError):
        rupees_to_paise("abc")


# ---------- invoice-number normalisation (FR-050) ----------

@pytest.mark.parametrize("raw, norm", [
    ("INV-2041", "INV2041"), ("inv 2041", "INV2041"), ("INV/2041", "INV2041"), ("INV-2041-R", "INV2041R"),
    ("SLST/2026/0512", "SLST20260512"), ("INV.SL.0451", "INVSL0451"), ("INV_2041", "INV_2041"),
])
def test_normalise_invoice_number(raw, norm):
    assert normalise_invoice_number(raw) == norm


def test_seed_ledger_normalised_numbers_agree():
    for rec in fixture_records("ledger.json"):
        assert normalise_invoice_number(rec["invoice_number_raw"]) == rec["invoice_number_normalised"]


# ---------- description normalisation (FR-064 AC 1-7, §9.5.1) ----------

@pytest.mark.parametrize("text, norm", [
    ("TMT Steel Rod 12 mm", "tmt steel rod 12mm"),            # AC1 (pairs with alias)
    ("2.5 sqmm", "2.5sqmm"),                                  # AC2
    ("12 mm", "12mm"),                                        # AC3
    ("Copper Cable 2.5 sqmm Coil", "copper cable 2.5sqmm coil"),  # AC4
    ("5-ply box", "5ply box"),                                # AC5
    ("TMT Bar. 16 mm", "tmt bar 16mm"),                       # AC6
])
def test_normalise_description_cases(text, norm):
    assert normalise_description(text) == norm


def test_ac1_pairs_with_po_alias():
    po_1042 = next(p for p in fixture_records("purchase_orders.json") if p["po_number"] == "PO-1042")
    tmt12 = po_1042["lines"][0]
    assert normalise_description("TMT Steel Rod 12 mm") in tmt12["aliases"]


@pytest.mark.parametrize("text", [
    "tmt steel rod 12mm", "2.5sqmm", "12mm", "copper cable 2.5sqmm coil", "5ply box", "tmt bar 16mm",
    "TMT Steel Rod 12 mm", "Copper Cable 2.5 sqmm Coil", "TMT Bar. 16 mm", "5-ply box",
])
def test_normalise_description_idempotent(text):   # AC7
    once = normalise_description(text)
    assert normalise_description(once) == once


def test_all_fixture_aliases_already_normalised():
    for po in fixture_records("purchase_orders.json"):
        for line in po["lines"]:
            for alias in line["aliases"]:
                assert normalise_description(alias) == alias


# ---------- masking (FR-041, DEC-001) ----------

@pytest.mark.parametrize("account, masked", [
    ("918020045514471", "XXXX XXXX 4471"), ("50200012341234", "XXXX XXXX 1234"),
    ("9180 2004 5514 471", "XXXX XXXX 4471"), ("000405512378", "XXXX XXXX 2378"),
])
def test_mask_account_number(account, masked):
    out = mask_account_number(account)
    assert out == masked
    assert account.replace(" ", "") not in out


def test_mask_short_and_none():
    assert mask_account_number(None) is None
    assert mask_account_number("1234") == "XXXX XXXX XXXX"


def test_ifsc_unchanged():
    for ifsc in ("HDFC0005512", "ICIC0005678", None):
        assert display_ifsc(ifsc) == ifsc
    assert masked_bank("918020045514471", "HDFC0005512") == "XXXX XXXX 4471 · HDFC0005512"


def test_every_vendor_account_masks_to_last_four():
    for v in fixture_records("vendors.json"):
        out = mask_account_number(v["bank_account_number"])
        assert out == "XXXX XXXX " + v["bank_account_number"][-4:]
