import copy
from collections import Counter

import pytest
import yaml

from src.claims import Claim, ClaimError, gold_claims, probe_claims
from src.snapshot import DEFAULT_SEED, FIXTURE_SEED, SeedError, expand_seed, write_catalog
from src.sources import SourceError
from src.splits import all_splits, leak_check, leave_one_category_out, temporal_split


# ───────────── seed / スナップショット ─────────────

def test_bench_seed_size_and_mix(bench):
    n = len(bench.products)
    assert 40 <= n <= 60
    regions = Counter(p["region"] for p in bench.products.values())
    assert regions["JP"] > 0 and regions["US"] > 0
    tops = Counter(p["category"].split(".")[0] for p in bench.products.values())
    assert tops["food"] + tops["cosmetics"] >= 15 and tops["electronics"] >= 20


def test_product_records_have_required_fields(bench):
    for p in bench.products.values():
        for k in ("product_id", "gtin", "gtin14", "brand", "category", "variant_axes", "region", "source_url",
                  "captured_at", "snapshot_id", "retailer"):
            assert p.get(k) not in (None, ""), (p["product_id"], k)
        assert p["source_url"].startswith("synthetic://")
        assert p["gtin14"][1] == "2" or p["gtin14"][2] == "2"     # GS1 店内用プレフィックス（実在商品と非衝突）


def test_snapshot_numeric_values_split_value_and_unit(bench):
    s = bench.snapshots["jp-kc-pd65-bk"]
    assert s["attributes"]["output_power"] == {"value": 65, "unit": "W"}
    assert s["region_spec"]["voltage"] == {"min": 100, "max": 240, "unit": "V"}
    assert s["compatibility"]["exhaustive"] is False
    assert bench.snapshots["jp-mz-ic80-c"]["compatibility"]["exhaustive"] is True
    # 商品側の上書き（地域版で内容量・成分が丸ごと異なる）
    assert bench.snapshots["us-senbei-soy"]["attributes"]["net_weight"]["unit"] == "oz"
    assert "pa_rating" not in bench.snapshots["us-sunscreen-1-4oz"]["region_spec"]   # null で削除


def test_seed_rejects_unregistered_or_blocked_source():
    seed = yaml.safe_load(open(FIXTURE_SEED, encoding="utf-8"))
    for sid in ("C1-manufacturer-spec-pages", "X1-customer-reviews-qa", "nope"):
        bad = dict(seed, source_id=sid)
        with pytest.raises(SourceError):
            expand_seed(bad)


def test_seed_rejects_bad_gtin_units_and_raw_text():
    seed = yaml.safe_load(open(FIXTURE_SEED, encoding="utf-8"))
    bad = copy.deepcopy(seed)
    bad["families"][0]["products"][0]["gtin"] = "2900101000000"
    with pytest.raises(SeedError):
        expand_seed(bad)
    bad = copy.deepcopy(seed)
    bad["families"][0]["attributes"]["width"]["unit"] = "cubits"
    with pytest.raises(SeedError):
        expand_seed(bad)
    bad = copy.deepcopy(seed)
    bad["families"][0]["page_text"] = "x"
    bad["families"][0]["products"][0]["attributes"] = {"note": {"value": "y" * 500}}
    with pytest.raises(SourceError):
        expand_seed(bad)


def test_write_catalog_outputs_jsonl_and_parquet(tmp_path, mini):
    import pandas as pd
    paths = write_catalog(mini, tmp_path / "m", tmp_path / "s")
    df = pd.read_parquet(paths["products_parquet"])
    assert len(df) == len(mini.products) and {"gtin14", "variant_axes", "captured_at"} <= set(df.columns)
    from src.snapshot import Catalog
    again = Catalog.from_jsonl(paths["products_jsonl"], paths["snapshots"])
    assert again.snapshots == mini.snapshots


# ───────────── claim スキーマ ─────────────

def test_claim_schema_validation():
    Claim(claim_id="x", claim_type="numeric", value=1, attribute="weight", unit="g")
    with pytest.raises(ClaimError):
        Claim(claim_id="x", claim_type="bogus", value=1)
    with pytest.raises(ClaimError):
        Claim(claim_id="x", claim_type="numeric", value=["heavy"], attribute="weight")
    with pytest.raises(ClaimError):
        Claim(claim_id="x", claim_type="numeric", value=1, attribute="w",
              label={"expected": "FAIL", "error_type": None, "rationale": {}})


def test_verifier_view_hides_labels(mini):
    c = gold_claims(mini)[0]
    v = c.verifier_view()
    assert "label" not in v and "product_id" not in v and "provenance" not in v
    assert Claim.from_dict(c.to_dict()) == c


def test_gold_claims_deterministic_and_labelled(bench):
    a, b = gold_claims(bench), gold_claims(bench)
    assert [x.to_dict() for x in a] == [y.to_dict() for y in b]
    assert len({c.claim_id for c in a}) == len(a)
    assert all(c.label["expected"] == "PASS" and c.severity for c in a)
    types = Counter(c.claim_type for c in a)
    for t in ("numeric", "model", "compatibility", "ingredient", "bundle", "variant", "region"):
        assert types[t] > 0, t
    # 表記揺れが実際に混ざっている（型番の全角、GTIN-14、日本語単位など）
    refs = [c.product_ref for c in a]
    assert any("model_number" in r and "－" in r["model_number"] for r in refs)
    assert any(len(r.get("gtin", "")) == 14 for r in refs)


def test_probe_claims_expect_unknown(bench):
    probes = probe_claims(bench)
    kinds = Counter(c.label["rationale"]["probe"] for c in probes)
    assert {"unknown-gtin", "ambiguous-model", "price", "ingredient-free-unverifiable"} <= set(kinds)
    assert all(c.label["expected"] == "UNKNOWN" for c in probes)


# ───────────── スプリットと漏れ検査 ─────────────

def test_splits_pass_leak_check(bench):
    from src.claims import challenge_claims
    from src.mutate import mutations
    claims = gold_claims(bench) + mutations(bench) + challenge_claims(bench)
    for s in all_splits(bench):
        assert leak_check(s, bench, claims) == [], s["split"]


def test_leak_check_flags_unflagged_borrowing(bench):
    from src.mutate import mutations
    s = copy.deepcopy(temporal_split(bench))
    fold = s["folds"][0]
    borrowed = next(c for c in mutations(bench)
                    if c.label["rationale"].get("source_product_id") in fold["train"] and c.product_id in fold["test"]
                    and bench.related_kind(c.product_id, c.label["rationale"]["source_product_id"]))
    fold["hard_negatives"] = [h for h in fold["hard_negatives"]
                              if (h["test"], h["train"]) != (borrowed.product_id,
                                                             borrowed.label["rationale"]["source_product_id"])]
    assert any("borrows a value" in i for i in leak_check(s, bench, [borrowed]))


def test_split_shapes(bench):
    loco = leave_one_category_out(bench)
    assert len(loco["folds"]) == len({p["category"] for p in bench.products.values()})
    t = temporal_split(bench, "2026-09-15")["folds"][0]
    assert "jp-yk-hd12-k" in t["test"]                         # 境界日は test 側
    assert t["train"] and t["test"]


def test_hard_negative_flags_are_correct(bench):
    t = temporal_split(bench, "2026-09-15")["folds"][0]
    kinds = {(h["test"], h["train"]): h["kind"] for h in t["hard_negatives"]}
    assert kinds[("jp-kc-pd65-wh", "jp-kc-pd65-bk")] == "sibling_variant"
    assert kinds[("us-kc-pd65-bk", "jp-kc-pd65-bk")] == "region_variant"
    assert kinds[("jp-kc-pd65-wh", "jp-kc-pd45-wh")] == "generation"
    assert all(bench.related_kind(a, b) == k for (a, b), k in kinds.items())


def test_leak_check_detects_problems(bench):
    s = copy.deepcopy(temporal_split(bench))
    fold = s["folds"][0]
    fold["train"].append(fold["test"][0])                          # 同一商品の跨り
    assert any("both train and test" in i for i in leak_check(s, bench))
    s = copy.deepcopy(temporal_split(bench))
    s["folds"][0]["hard_negatives"].pop()                          # フラグ漏れ
    assert any("without hard_negative flag" in i for i in leak_check(s, bench))
    s = copy.deepcopy(temporal_split(bench))
    h = s["folds"][0]["hard_negatives"][0]
    h["kind"] = "generation" if h["kind"] != "generation" else "sibling_variant"   # 種別の誤り
    assert any("wrong kind" in i for i in leak_check(s, bench))
