import re

from app.policy import load_policy
from tests.conftest import fixture_records

VENDORS = fixture_records("vendors.json")


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_meta(client):
    body = client.get("/api/meta").json()
    assert body["product"] == "InvoPilot"
    assert body["product_version"]
    assert body["policy_version"] == load_policy().version
    assert body["fixture_version"] == "fixtures-v1"
    assert body["scope"] == {
        "currency": "INR", "tax_regime": "GST", "buyer_state_code": "29",
        "matching": "two_way", "processing": "sequential_single_worker", "synthetic_data": True,
    }


def test_seed_status(client):
    assert client.get("/api/seed-status").json()["counts"] == {
        "vendors": 7, "purchase_orders": 11, "po_lines": 15, "ledger": 5, "consumption": 5,
    }


def test_startup_seeds_only_once(settings):
    from fastapi.testclient import TestClient
    from app.main import create_app

    for _ in range(2):
        with TestClient(create_app(settings)) as c:
            assert c.get("/api/seed-status").json()["counts"]["vendors"] == 7


def _pos(client):
    return {p["po_number"]: p for p in client.get("/api/pos").json()}


def test_pos_balances(client):
    pos = _pos(client)
    assert len(pos) == 11

    p1041 = pos["PO-1041"]
    assert p1041["po_total"] == {"paise": 30000000, "display": "₹3,00,000.00"}
    assert p1041["approved"] == {"paise": 22000000, "display": "₹2,20,000.00"}
    assert p1041["remaining"] == {"paise": 8000000, "display": "₹80,000.00"}
    assert p1041["allowed_overrun"]["paise"] == 100000          # min(3,000, 1,000)
    assert p1041["ceiling"]["paise"] == 30100000                # ₹3,01,000

    p1042 = pos["PO-1042"]
    assert p1042["approved"]["paise"] == 0
    assert p1042["remaining"] == {"paise": 12500000, "display": "₹1,25,000.00"}
    assert p1042["ceiling"]["paise"] == 12600000                # ₹1,26,000 (T-01)

    assert pos["PO-1044"]["allowed_overrun"]["paise"] == 84000  # 1% of ₹84,000 < ₹1,000
    assert pos["PO-1045"]["ceiling"]["paise"] == 10100000       # ₹1,01,000 (T-25)
    assert pos["PO-1047"]["remaining"]["paise"] == 36000000 - 9000000
    for p in pos.values():
        assert p["remaining"]["paise"] >= 0
        assert p["remaining"]["paise"] == p["po_total"]["paise"] - p["approved"]["paise"]


def test_vendors_masked_and_ifsc_full(client):
    vendors = {v["vendor_id"]: v for v in client.get("/api/vendors").json()}
    assert len(vendors) == 7
    for fx in VENDORS:
        v = vendors[fx["vendor_id"]]
        assert v["bank_account_masked"] == "XXXX XXXX " + fx["bank_account_number"][-4:]
        assert v["ifsc"] == fx["ifsc"]
        assert v["status"] == fx["status"]
    assert vendors["V003"]["bank_display"] == "XXXX XXXX 4471 · HDFC0005512"


def test_no_full_account_number_in_any_response(client):
    for path in ("/api/vendors", "/api/pos", "/api/meta", "/api/seed-status"):
        text = client.get(path).text
        for fx in VENDORS:
            assert fx["bank_account_number"] not in text, path
    # PRD §13.3: no 9+ digit account-like number in the vendor payload.
    assert not re.search(r"\d{9,}", client.get("/api/vendors").text)


def test_pos_response_has_no_long_account_like_digits(client):
    # /api/pos contains large paise integers; strip those fields and check nothing else has 9+ digits.
    for p in client.get("/api/pos").json():
        for key, val in p.items():
            if isinstance(val, dict):
                continue
            assert not re.search(r"\d{9,}", str(val)), (key, val)
        assert "bank_account_number" not in p


def test_vendors_exclude_trusted_contact(client):
    body = client.get("/api/vendors")
    text = body.text
    for v in body.json():
        assert not any(k.startswith("trusted_contact") for k in v)
        assert "bank_account_number" not in v
    for fx in VENDORS:
        assert fx["trusted_contact_phone"] not in text
        assert fx["trusted_contact_email"] not in text
        digits = re.sub(r"\D", "", fx["trusted_contact_phone"])
        assert digits not in re.sub(r"\D", "", text)
