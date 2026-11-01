import pytest
from fastapi.testclient import TestClient

from src.snapshot import FIXTURE_SEED


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("SPECSURE_SEED", str(FIXTURE_SEED))
    from src.api import app
    return TestClient(app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["products"] == 9


def test_verify_block_with_evidence(client, mini):
    gtin = mini.products["fx-choco-jp"]["gtin"]
    body = {"claims": [
        {"claim_id": "a", "claim_type": "numeric", "value": 50, "unit": "g", "attribute": "net_weight",
         "product_ref": {"gtin": gtin}},
        {"claim_id": "b", "claim_type": "ingredient", "value": "milk", "attribute": "contains", "polarity": "deny",
         "product_ref": {"gtin": gtin}},
    ]}
    r = client.post("/verify", json=body)
    assert r.status_code == 200
    out = r.json()
    assert out["decision"] == "BLOCK"
    assert out["products"][0]["product_id"] == "fx-choco-jp"
    assert out["products"][0]["blocking_claims"] == ["b"]
    res = {c["claim_id"]: c for c in out["claims"]}
    assert res["a"]["verdict"] == "PASS"
    assert res["b"]["verdict"] == "FAIL" and res["b"]["severity"] == "S1"
    assert res["b"]["evidence"][0]["snapshot_id"].startswith("snap-fx-choco-jp")


def test_verify_pass_and_model_lookup(client):
    body = {"claims": [{"claim_id": "a", "claim_type": "numeric", "value": "65W", "attribute": "output_power",
                        "product_ref": {"model_number": "kc pd65 bk"}}]}
    out = client.post("/verify", json=body).json()
    assert out["decision"] == "PASS" and out["claims"][0]["verdict"] == "PASS"


def test_verify_rejects_bad_input(client):
    assert client.post("/verify", json={"claims": []}).status_code == 422
    bad = {"claims": [{"claim_id": "a", "claim_type": "rumor", "value": 1}]}
    assert client.post("/verify", json=bad).status_code == 422
    bad = {"claims": [{"claim_id": "a", "claim_type": "numeric", "value": 1}]}            # attribute 必須
    assert client.post("/verify", json=bad).status_code == 422
