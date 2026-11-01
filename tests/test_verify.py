import pytest

from src.claims import Claim, gold_claims, probe_claims
from src.mutate import mutations
from src.verify import ClaimResult, Verifier


@pytest.fixture(scope="module")
def v(mini):
    return Verifier(mini)


def C(pid_ref, ctype, value, **kw):
    return Claim(claim_id=kw.pop("cid", "t"), claim_type=ctype, value=value, product_ref=pid_ref, **kw)


def ref(cat, pid, by="gtin"):
    p = cat.products[pid]
    return {"gtin": p["gtin"]} if by == "gtin" else {"model_number": p["model_number"]}


# ───────────── gold は PASS、mutation は FAIL、証拠なしは UNKNOWN ─────────────

def test_fixture_gold_all_pass(mini, v):
    for c in gold_claims(mini):
        r = v.verify_claim(c)
        assert r.verdict == "PASS", (c.claim_id, r.reason)


def test_fixture_mutations_all_fail_with_evidence(mini, v):
    muts = mutations(mini)
    assert {c.label["error_type"] for c in muts} >= {"E1", "E2", "E3", "E4", "E5", "E7", "E8"}
    for c in muts:
        r = v.verify_claim(c)
        assert r.verdict == "FAIL", (c.claim_id, r.reason)
        assert r.evidence and all("snapshot_id" in e and "path" in e for e in r.evidence)


def test_fixture_probes_unknown(mini, v):
    for c in probe_claims(mini):
        assert v.verify_claim(c).verdict == "UNKNOWN", c.claim_id


# ───────────── 類型別ケース ─────────────

def test_e1_generation_value(mini, v):
    r = v.verify_claim(C(ref(mini, "fx-c45-wh", "model"), "numeric", 65, attribute="output_power", unit="W"))
    assert r.verdict == "FAIL" and r.diagnosis["error_type"] == "E1" and r.outcome == "BLOCK"
    r = v.verify_claim(C(ref(mini, "fx-c45-wh"), "model", "KC-PD65-WH"))
    assert r.verdict == "FAIL" and r.diagnosis["error_type"] == "E1"


def test_e2_variant(mini, v):
    r = v.verify_claim(C(ref(mini, "fx-c65-bk"), "variant", "ホワイト", attribute="color"))
    assert r.verdict == "FAIL" and r.diagnosis["error_type"] == "E2"
    assert v.verify_claim(C(ref(mini, "fx-c65-bk"), "variant", "ブラック", attribute="color")).verdict == "PASS"
    r = v.verify_claim(C(ref(mini, "fx-c65-bk"), "model", "KC-PD65-WH"))
    assert r.verdict == "FAIL" and r.diagnosis["error_type"] == "E2"


def test_e3_numeric_and_unit_swap(mini, v):
    p = ref(mini, "fx-c65-bk")
    assert v.verify_claim(C(p, "numeric", 4.5, attribute="width", unit="cm")).verdict == "PASS"
    assert v.verify_claim(C(p, "numeric", "45.5mm", attribute="width")).verdict == "PASS"      # 境界 0.5 mm
    assert v.verify_claim(C(p, "numeric", 45.6, attribute="width", unit="mm")).verdict == "FAIL"
    r = v.verify_claim(C(p, "numeric", 45, attribute="width", unit="in"))                     # inch と mm の取り違え
    assert r.verdict == "FAIL" and r.diagnosis == {"error_type": "E3", "basis": "same_number_different_unit"}
    r = v.verify_claim(C(p, "numeric", 45, attribute="width", unit="g"))
    assert r.verdict == "FAIL" and r.reason == "unit_dimension_mismatch"
    assert v.verify_claim(C(p, "numeric", 45, attribute="width", unit="cubits")).verdict == "UNKNOWN"


def test_e4_compatibility_exhaustive_vs_not(mini, v):
    ink = ref(mini, "fx-ink-bk")
    assert v.verify_claim(C(ink, "compatibility", "ミズナギ PX-810")).verdict == "PASS"
    assert v.verify_claim(C(ink, "compatibility", "Mizunagi PX-710")).verdict == "FAIL"           # 明示的に非対応
    r = v.verify_claim(C(ink, "compatibility", "Mizunagi PX-999"))                                 # 網羅的リスト外
    assert r.verdict == "FAIL" and r.reason == "not_in_exhaustive_list" and r.evidence[0]["exhaustive"]
    assert v.verify_claim(C(ink, "compatibility", "Mizunagi PX-820", polarity="deny")).verdict == "FAIL"
    chg = ref(mini, "fx-c65-bk")
    r = v.verify_claim(C(chg, "compatibility", "Ostrel BookPro 15"))                               # 網羅的でない
    # Phase 2 ポリシー変更: S2 の UNKNOWN は PASS → WARN（taxonomy.yaml aggregation_policy）
    assert r.verdict == "UNKNOWN" and r.outcome == "WARN"
    assert v.verify_claim(C(chg, "compatibility", "Corvel TurboCharge")).verdict == "FAIL"


def test_e5_bundle(mini, v):
    p = ref(mini, "fx-c65-bk")
    assert v.verify_claim(C(p, "bundle", "user manual")).verdict == "PASS"
    r = v.verify_claim(C(p, "bundle", "ACアダプター"))
    assert r.verdict == "FAIL" and r.outcome == "WARN"                                               # S3 → WARN


def test_e7_ingredients(mini, v):
    jp = ref(mini, "fx-choco-jp")
    assert v.verify_claim(C(jp, "ingredient", "milk")).verdict == "PASS"                            # 全粉乳 → 乳
    r = v.verify_claim(C(jp, "ingredient", "dairy", polarity="deny"))
    assert r.verdict == "FAIL" and r.outcome == "BLOCK" and r.severity == "S1"
    r = v.verify_claim(C(jp, "ingredient", "peanut", polarity="deny"))                            # 含む可能性表示
    assert r.verdict == "FAIL" and r.reason == "may_contain_conflict"
    assert v.verify_claim(C(jp, "ingredient", "egg", polarity="deny")).verdict == "PASS"
    assert v.verify_claim(C(jp, "ingredient", "honey")).verdict == "FAIL"                           # 網羅的で不在
    r = v.verify_claim(C(jp, "ingredient", "lactose", polarity="deny"))                           # 主張語が辞書外
    assert r.verdict == "UNKNOWN" and r.outcome == "BLOCK"


def test_s1_unknown_blocks(mini, v):
    p = ref(mini, "fx-lotion-partial")                                                          # 主要成分のみ
    r = v.verify_claim(C(p, "ingredient", "paraben", polarity="deny"))
    assert r.verdict == "UNKNOWN" and r.severity == "S1" and r.outcome == "BLOCK"
    assert v.verify_claim(C(p, "ingredient", "glycerin")).verdict == "PASS"
    rep = v.verify([C(p, "ingredient", "glycerin", cid="a"), C(p, "ingredient", "paraben", polarity="deny", cid="b")])
    assert rep["decision"] == "BLOCK" and rep["products"][0]["blocking_claims"] == ["b"]


def test_e8_region(mini, v):
    jp = ref(mini, "fx-dryer-jp", "model")
    r = v.verify_claim(C(jp, "region", 120, attribute="voltage", unit="V"))
    assert r.verdict == "FAIL" and r.diagnosis["error_type"] == "E8"
    assert v.verify_claim(C(jp, "region", "100 V", attribute="voltage")).verdict == "PASS"
    chg = ref(mini, "fx-c65-bk")
    assert v.verify_claim(C(chg, "region", 120, attribute="voltage", unit="V")).verdict == "PASS"     # 100–240 V の範囲内
    assert v.verify_claim(C(chg, "region", {"min": 100, "max": 240}, attribute="voltage", unit="V")).verdict == "PASS"
    r = v.verify_claim(C(ref(mini, "fx-choco-us"), "numeric", 50, attribute="net_weight", unit="g"))
    assert r.verdict == "PASS"                                                                  # 1.76 oz = 49.9 g


def test_identity_failures_are_unknown(mini, v):
    assert v.verify_claim(C({"gtin": "2900101000005"}, "numeric", 45, attribute="output_power", unit="W")).verdict \
        == "UNKNOWN"
    r = v.verify_claim(C({"model_number": "KC-PD65"}, "numeric", 65, attribute="output_power", unit="W"))
    assert r.verdict == "UNKNOWN" and r.reason == "identity_ambiguous"
    assert v.verify_claim(C({}, "numeric", 65, attribute="output_power", unit="W")).verdict == "UNKNOWN"
    # 型番の表記揺れ（全角）は同定できる
    assert v.verify_claim(C({"model_number": "ＫＣ－ＰＤ６５－ＢＫ"}, "numeric", 65, attribute="output_power",
                            unit="W")).verdict == "PASS"


def test_price_out_of_scope(mini, v):
    assert v.verify_claim(C(ref(mini, "fx-c65-bk"), "price", 29.99, attribute="price")).verdict == "UNKNOWN"


def test_fail_without_evidence_is_suppressed(mini, v):
    r = v._finish(ClaimResult("z", "FAIL", "made_up", "S2"))
    # Phase 2 ポリシー変更: S2 の UNKNOWN は PASS → WARN
    assert r.verdict == "UNKNOWN" and r.outcome == "WARN" and "suppressed" in r.reason


def test_aggregation_policy(mini, v):
    p = ref(mini, "fx-c65-bk")
    s3 = C(p, "bundle", "ACアダプター", cid="s3")
    s2 = C(p, "numeric", 45, attribute="output_power", unit="W", cid="s2")
    ok = C(p, "numeric", 65, attribute="output_power", unit="W", cid="ok")
    assert v.verify([ok])["decision"] == "PASS"
    assert v.verify([ok, s3])["decision"] == "WARN"
    assert v.verify([ok, s3, s2])["decision"] == "BLOCK"
    # 入力側の重大度ヒントが厳しければそちらを採用（S3 の同梱物でも S1 と言われれば S1）
    r = v.verify_claim(C(p, "bundle", "ACアダプター", severity="S1"))
    assert r.severity == "S1" and r.outcome == "BLOCK"


def test_multi_product_report(mini, v):
    rep = v.verify([C(ref(mini, "fx-c65-bk"), "numeric", 65, attribute="output_power", unit="W", cid="a"),
                    C(ref(mini, "fx-ink-bk"), "compatibility", "Mizunagi PX-710", cid="b"),
                    C({"gtin": "123"}, "numeric", 1, attribute="weight", unit="g", cid="c")])
    by = {p["product_id"]: p["decision"] for p in rep["products"]}
    assert by == {"fx-c65-bk": "PASS", "fx-ink-bk": "BLOCK", None: "PASS"}
    assert rep["decision"] == "BLOCK"
