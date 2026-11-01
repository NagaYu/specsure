"""Phase 2 Step 0: taxonomy の正本化、S2 UNKNOWN→WARN、表記精度フロア、語彙・同一性の弱点修正、E7 診断。"""

import pytest

from src.claims import Claim, gold_claims
from src.identity import parse_model
from src.normalize import decimal_step, load_config, parse_quantity, precision_floor_si, vocab
from src.verify import Verifier


@pytest.fixture(scope="module")
def vb(bench):
    return Verifier(bench)


def C(ref, ctype, value, **kw):
    return Claim(claim_id=kw.pop("cid", "t"), claim_type=ctype, value=value, product_ref=ref, **kw)


def ref(cat, pid, by="gtin"):
    p = cat.products[pid]
    return {"gtin": p["gtin"]} if by == "gtin" else {"model_number": p["model_number"]}


# ───────────── 0-1 taxonomy ─────────────

def test_taxonomy_ratified_names():
    tax = load_config("taxonomy.yaml")
    names = {k: v["name"] for k, v in tax["error_types"].items()}
    assert names == {"E1": "型番・世代混同", "E2": "バリアント取り違え", "E3": "数値捏造・単位ミス",
                     "E4": "互換性の捏造", "E5": "同梱物・数量の誤り", "E6": "価格・在庫の陳腐化",
                     "E7": "成分・アレルゲンの誤り", "E8": "地域仕様の混同"}
    assert {k: v["name"] for k, v in tax["severities"].items()} == {
        "S1": "安全・法務", "S2": "購入を無効にする", "S3": "体験を損なう"}
    assert tax["error_types"]["E7"]["severity"] == "S1"
    assert tax["severity_rules"]["by_claim_type"]["ingredient"] == "S1"


def test_mandatory_allergens_not_extended():
    # 法的確認待ち（provisional）。Phase 2 で拡張しない
    m = vocab().mandatory_allergens
    assert len(m["JP"]) == 8 and len(m["US"]) == 9


# ───────────── 0-2 集約ポリシー ─────────────

def test_aggregation_unknown_by_severity(mini):
    v = Verifier(mini)
    chg = ref(mini, "fx-c65-bk")
    s2 = C(chg, "compatibility", "Ostrel BookPro 15", cid="s2")                       # 網羅的でない → UNKNOWN
    s3 = C(chg, "numeric", 10, attribute="battery_life", unit="h", cid="s3")         # 属性なし → UNKNOWN (S3)
    s1 = C(ref(mini, "fx-lotion-partial"), "ingredient", "paraben", polarity="deny", cid="s1")
    r2, r3, r1 = (v.verify_claim(c) for c in (s2, s3, s1))
    assert (r2.verdict, r2.severity, r2.outcome) == ("UNKNOWN", "S2", "WARN")
    assert (r3.verdict, r3.severity, r3.outcome) == ("UNKNOWN", "S3", "PASS")
    assert (r1.verdict, r1.severity, r1.outcome) == ("UNKNOWN", "S1", "BLOCK")
    rep = v.verify([s2, s3])
    p = rep["products"][0]
    assert rep["decision"] == "WARN" and p["warning_claims"] == ["s2"] and p["unverified_claims"] == ["s3"]


# ───────────── 0-3 表記精度フロア ─────────────

def test_decimal_step_and_floor():
    assert decimal_step("6.6") == pytest.approx(0.1)
    assert decimal_step(6.0) == pytest.approx(0.1)
    assert decimal_step(400) == 1 and decimal_step("20,000") == 1
    assert precision_floor_si("6.6", "ft", "length") == pytest.approx(0.05 * 0.3048)


def test_cable_6_6_ft_passes_6_0_ft_fails(bench, vb):
    r = ref(bench, "jp-pw-h21-2m", "model")                                          # 2 m（±10 mm）
    assert vb.verify_claim(C(r, "numeric", 6.6, attribute="length", unit="ft")).verdict == "PASS"
    assert vb.verify_claim(C(r, "numeric", "6.6 ft", attribute="length")).verdict == "PASS"
    assert vb.verify_claim(C(r, "numeric", 6.0, attribute="length", unit="ft")).verdict == "FAIL"
    # 整数表記は最終桁が 1 ft なのでフロア ±0.5 ft。「7 ft」(2.13 m) は 2 m を含む → PASS（規則上の仕様。緩さは既知）
    assert vb.verify_claim(C(r, "numeric", 7, attribute="length", unit="ft")).verdict == "PASS"
    assert vb.verify_claim(C(r, "numeric", 8, attribute="length", unit="ft")).verdict == "FAIL"


def test_floor_does_not_loosen_precise_claims(bench, vb):
    r = ref(bench, "jp-kc-pd65-bk")
    assert vb.verify_claim(C(r, "numeric", 64, attribute="output_power", unit="W")).verdict == "FAIL"
    assert vb.verify_claim(C(r, "numeric", 64.9, attribute="output_power", unit="W")).verdict == "FAIL"


# ───────────── 0-4 語彙と同一性 ─────────────

def test_color_variants_match_but_are_not_used_for_gold_surface(bench, vb):
    r = ref(bench, "jp-se-20-wh", "model")
    assert vb.verify_claim(C(r, "variant", "パールホワイト", attribute="color")).verdict == "PASS"
    assert vb.verify_claim(C(r, "variant", "pearl white", attribute="color")).verdict == "PASS"
    assert vb.verify_claim(C(r, "variant", "ジェットブラック", attribute="color")).verdict == "FAIL"
    surfaces = {str(c.value) for c in gold_claims(bench) if c.claim_type == "variant" and c.attribute == "color"}
    assert not surfaces & {"pearl white", "パールホワイト", "jet black", "ジェットブラック", "matte black", "off white"}


def test_brandless_compat_resolves_only_when_unique(bench, vb):
    n5 = ref(bench, "us-hv-n5-cl", "model")                                          # 網羅的: [Corvel Nova 5]
    r = vb.verify_claim(C(n5, "compatibility", "Nova 5"))
    assert r.verdict == "PASS" and r.detail["resolved_from"] == "Nova 5"
    assert vb.verify_claim(C(n5, "compatibility", "Nova 5 Pro")).verdict == "FAIL"       # 一意に解決 → 非対応
    assert vb.verify_claim(C(n5, "compatibility", "Nova")).verdict == "UNKNOWN"          # 曖昧（部分一致）
    ink = ref(bench, "jp-mz-ic80-c", "model")
    assert vb.verify_claim(C(ink, "compatibility", "PX-820 printer")).verdict == "PASS"
    assert vb.verify_claim(C(ink, "compatibility", "PX-700 プリンター")).verdict == "FAIL"  # 前世代の機種
    bat = ref(bench, "us-tr-lb-56wh", "model")
    assert vb.verify_claim(C(bat, "compatibility", "BookAir 14 2025")).verdict == "PASS"
    r = vb.verify_claim(C(bat, "compatibility", "BookAir 14"))                         # 2024 / 2025 で曖昧
    assert r.verdict == "UNKNOWN" and r.reason.startswith("compat_item_ambiguous")
    # ブランド付きで未知の機種は従来どおり（網羅的リストなら FAIL）
    assert vb.verify_claim(C(bat, "compatibility", "Ostrel BookAir 16 (2026)")).verdict == "FAIL"


def test_model_with_color_word_suffix(bench, vb):
    assert parse_model("KP-20K (black)").full == parse_model("KP-20K-BK").full
    r = ref(bench, "jp-kp-20k-bk")
    assert vb.verify_claim(C(r, "model", "KP-20K (black)")).verdict == "PASS"
    assert vb.verify_claim(C(r, "model", "KP-20K (white)")).verdict == "FAIL"


def test_value_prefixes_and_unit_words(bench, vb):
    assert parse_quantity("約8時間") == (8.0, "時間")
    assert parse_quantity("AC100V") == (100.0, "V")
    assert parse_quantity("approx. 112 g") == (112.0, "g")
    assert vb.verify_claim(C(ref(bench, "jp-se-20-bk"), "numeric", "約8時間", attribute="battery_life")).verdict == "PASS"
    assert vb.verify_claim(C(ref(bench, "jp-yk-hd12-w"), "region", "AC100V", attribute="voltage")).verdict == "PASS"
    assert vb.verify_claim(C(ref(bench, "jp-kc-pd65-bk"), "numeric", 65, attribute="output_power",
                             unit="watts")).verdict == "PASS"


# ───────────── 0-5 E7 診断 ─────────────

def test_e7_diagnosis_on_unknown_absence(bench, vb):
    r = vb.verify_claim(C(ref(bench, "jp-sunscreen-80g"), "ingredient", "alcohol", polarity="deny"))
    assert r.verdict == "UNKNOWN" and r.outcome == "BLOCK"
    assert r.diagnosis == {"error_type": "E7", "basis": "absence_unverifiable(list_not_exhaustive)",
                           "kind": "unverified_risk"}
    r = vb.verify_claim(C(ref(bench, "jp-choco-milk"), "ingredient", "lactose", polarity="deny"))
    assert r.verdict == "UNKNOWN" and r.diagnosis["error_type"] == "E7"
    ok = vb.verify_claim(C(ref(bench, "jp-choco-milk"), "ingredient", "milk"))
    assert ok.verdict == "PASS" and ok.diagnosis is None


# ───────────── Phase 3 Step 1: E5 数量（pack_count） ─────────────

def test_pack_count_severity_and_verification(bench, vb):
    from src.claims import severity_for
    assert severity_for("numeric", "pack_count") == "S2"
    r = ref(bench, "jp-senbei-shoyu")                                                 # 個包装 10 袋
    assert vb.verify_claim(C(r, "numeric", "10袋", attribute="pack_count")).verdict == "PASS"
    assert vb.verify_claim(C(r, "numeric", 10, attribute="pack_count", unit="pcs")).verdict == "PASS"
    bad = vb.verify_claim(C(r, "numeric", "12本入り", attribute="pack_count"))
    assert bad.verdict == "FAIL" and bad.outcome == "BLOCK" and bad.diagnosis["error_type"] == "E5"
    assert vb.verify_claim(C(ref(bench, "us-granola-orig-24oz"), "numeric", "2-pack",
                             attribute="pack_count")).verdict == "PASS"


def test_pack_count_mutations_are_e5(bench):
    from src.mutate import mutations
    pc = [c for c in mutations(bench) if c.attribute == "pack_count"]
    assert pc and all(c.label["error_type"] == "E5" and c.severity == "S2" for c in pc)
    assert not [c for c in mutations(bench) if c.attribute == "pack_count" and c.label["error_type"] == "E3"]


def test_pack_count_extraction():
    from src.extract import extract

    def packs(t):
        return [(e.claim.attribute, e.claim.value) for e in extract(t).claims if e.claim.attribute == "pack_count"]
    assert packs("替えブラシ12本セットです。") == [("pack_count", 12)]
    assert packs("2個入りです。") == [("pack_count", 2)]
    assert packs("It comes in a pack of 12.") == [("pack_count", 12)]
    assert packs("This 12-pack is a good deal.") == [("pack_count", 12)]
    assert packs("重さは12gです。") == []
