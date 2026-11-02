"""Sample invoice PDFs (Day 2A): every PDF verifies against the fixtures, and the verifier
itself is shown to catch each class of mistake (mutation tests)."""
import copy
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("verify", REPO / "invoice-templates" / "verify.py")
verify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verify)

CTX = verify.Ctx()
SUMS = verify.read_sums(CTX)
BY_ID = {i["test_id"]: i for i in CTX.invoices}


@pytest.mark.parametrize("test_id", sorted(BY_ID))
def test_invoice_verifies(test_id):
    assert verify.check_invoice(BY_ID[test_id], CTX, SUMS) == []


def test_set_rules():
    assert verify.check_set(CTX) == []
    assert verify.REQUIRED_TESTS <= set(BY_ID)
    assert len({i["layout"] for i in CTX.invoices}) >= 4


def test_every_pdf_in_sums():
    pdfs = {p.name for p in CTX.samples.glob("*.pdf")}
    assert pdfs == set(SUMS) == {i["file"] for i in CTX.invoices}


# ---------- specific PRD / README_FIXTURES facts, asserted directly ----------

def test_t01_alias_pairing_and_master_bank():
    inv = BY_ID["T-01"]
    pairs, unpaired = verify.pair_lines(inv, CTX.pos["PO-1042"])
    assert [(p[1]["po_line_id"], p[2]) for p in pairs] == [("PO-1042-1", "alias"), ("PO-1042-2", "code")]
    assert not unpaired
    v001 = CTX.vendors["V001"]
    assert (inv["bank"]["account_number"], inv["bank"]["ifsc"]) == (v001["bank_account_number"], v001["ifsc"])


def test_t02_dated_29_oct():
    assert BY_ID["T-02"]["invoice_date"] == "2026-10-29"


def test_t03_bank():
    b = BY_ID["T-03"]["bank"]
    assert b["account_number"].endswith("4471") and b["ifsc"] == "ICIC0005678"
    assert CTX.vendors["V003"]["ifsc"] == "HDFC0005512"


def test_t15_e4_off_by_exactly_1000():
    assert verify.arithmetic(BY_ID["T-15"])["E4"] == 100000


def test_t12_cutting_charges_pairs_with_nothing():
    _, unpaired = verify.pair_lines(BY_ID["T-12"], CTX.pos["PO-1041"])
    assert [l["description"] for l in unpaired] == ["Cutting charges"]


def test_duplicate_tiers():
    expected = {"T-02": "similar_note", "T-05": "confirmed", "T-06": "similar_note", "T-07": "revision",
                "T-16": "possible", "T-09": "not_checked"}
    for tid, inv in BY_ID.items():
        assert verify.duplicate_tiers(inv, CTX) == {expected.get(tid, "none")}, tid


def test_t05_is_image_only_and_others_have_text():
    import pymupdf

    for inv in CTX.invoices:
        doc = pymupdf.open(CTX.samples / inv["file"])
        text = "".join(p.get_text() for p in doc).strip()
        assert (text == "") == (inv["test_id"] == "T-05"), inv["test_id"]
        doc.close()


# ---------- mutation tests: the verifier must catch each mistake ----------

def mutated(test_id, change):
    inv = copy.deepcopy(BY_ID[test_id])
    change(inv)
    return verify.check_invoice(inv, CTX, SUMS)


def assert_caught(failures, fragment):
    assert any(fragment in f for f in failures), failures


def test_catches_wrong_t01_bank():
    def c(i): i["bank"]["ifsc"] = "HDFC0009999"
    f = mutated("T-01", c)
    assert_caught(f, "!= vendor master")
    assert_caught(f, "T-01 bank must be")


def test_catches_t03_matching_ifsc():
    def c(i): i["bank"]["ifsc"] = "HDFC0005512"
    assert_caught(mutated("T-03", c), "should differ from master")


def test_catches_t02_inside_duplicate_window():
    def c(i): i["invoice_date"] = "2026-10-20"
    f = mutated("T-02", c)
    assert_caught(f, "duplicate tiers ['possible']")
    assert_caught(f, "T-02 must be dated")


@pytest.mark.parametrize("test_id, new_date, fragment", [
    ("T-16", "2026-10-20", "duplicate tiers ['similar_note']"),   # outside the 7-day window
    ("T-06", "2026-10-05", "duplicate tiers ['possible']"),       # inside the window: false duplicate
    ("T-04", "2026-10-05", "outside PO-1036 window"),             # before PO-1036 date
    ("T-01", "2027-01-15", "implausible"),                        # future
])
def test_catches_date_window_breaks(test_id, new_date, fragment):
    def c(i): i["invoice_date"] = new_date
    assert_caught(mutated(test_id, c), fragment)


def test_catches_t15_wrong_variance():
    def c(i): i["total"] = "8669.00"
    assert_caught(mutated("T-15", c), "E4 variance 999.00 != intended 1000.00")


def test_catches_unintended_arithmetic_error():
    def c(i): i["tax"]["cgst"]["amount"] = "8560.00"
    f = mutated("T-01", c)
    assert_caught(f, "E3.cgst off by 10.00")
    assert_caught(f, "E4 off by -10.00")          # printed total is now Rs 10 below the computed one


def test_catches_t08_without_variance():
    def c(i):
        i["lines"][0]["unit_price"] = "1000.00"
        i["lines"][0]["amount"] = "80000.00"
    assert_caught(mutated("T-08", c), "price variance expected")


def test_catches_unexpected_price_difference():
    def c(i):
        i["lines"][0]["unit_price"] = "651.00"
        i["lines"][0]["amount"] = "52080.00"
    assert_caught(mutated("T-01", c), "unit price 651.00 != PO unit price 650.00")


def test_catches_unpairable_line():
    def c(i): i["lines"][0]["description"] = "TMT Steel Rod 12 mm premium"
    assert_caught(mutated("T-01", c), "unpaired lines ['TMT Steel Rod 12 mm premium']")


def test_catches_t12_cutting_charges_pairing():
    def c(i): i["expect"]["unpaired_lines"] = []
    assert_caught(mutated("T-12", c), "unpaired lines ['Cutting charges']")


def test_catches_t09_known_gstin():
    def c(i): i["vendor"]["gstin"] = "29AABCS1234F1Z5"
    assert_caught(mutated("T-09", c), "matches fixture vendor V001")


def test_catches_po_of_other_vendor():
    def c(i): i["po_reference"] = "PO-1044"
    assert_caught(mutated("T-01", c), "belongs to V003")


def test_catches_wrong_tax_regime():
    def c(i):
        i["tax"] = {"cgst": {"rate": 9, "amount": "3780.00"}, "sgst": {"rate": 9, "amount": "3780.00"}, "igst": None}
    assert_caught(mutated("T-03", c), "inter-state invoice must charge IGST only")


def test_catches_pdf_not_matching_manifest():
    def c(i): i["invoice_number"] = "SLST/2026/0513"
    assert_caught(mutated("T-01", c), "PDF text does not contain 'SLST/2026/0513'")


def test_catches_text_layer_on_scan():
    def c(i):
        i["file"] = BY_ID["T-07"]["file"]
    assert_caught(mutated("T-05", c), "scan has an extractable text layer")


def test_catches_size_and_page_limits(monkeypatch):
    monkeypatch.setattr(CTX, "max_bytes", 1000)
    monkeypatch.setattr(CTX, "max_pages", 0)
    f = verify.check_invoice(BY_ID["T-01"], CTX, SUMS)
    assert_caught(f, "(A-23)")
    assert sum("(A-23)" in x for x in f) == 2


def test_catches_stale_pdf():
    f = verify.check_invoice(BY_ID["T-01"], CTX, {**SUMS, BY_ID["T-01"]["file"]: "0" * 64})
    assert_caught(f, "does not match SHA256SUMS")


def test_catches_scan_not_matching_ledger():
    def c(i): i["total"] = "106201.00"
    assert_caught(mutated("T-05", c), "scan total")
