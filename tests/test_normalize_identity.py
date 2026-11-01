import pytest

from src.identity import (IdentityIndex, Relation, ResolveStatus, compare_models, gtin_check_digit,
                          normalize_gtin, parse_model)
from src.normalize import parse_quantity, text_key, tolerances, units, vocab


# ───────────── 型番正規化 ─────────────

@pytest.mark.parametrize("raw", ["KC-PD65-BK", "kc-pd65-bk", "ＫＣ－ＰＤ６５－ＢＫ", "KC PD65 BK", "KC‐PD65ーBK",
                                 "KC_PD65/BK", "Kestrelon KC-PD65-BK", "KC-PD65(BK)"])
def test_model_surface_variants_normalize_to_same_key(raw):
    assert parse_model(raw, ["Kestrelon"]).full == parse_model("KC-PD65-BK").full


def test_suffix_separation_color_region_capacity_branch():
    k = parse_model("KC-PD65-BK-US")
    assert k.base == "KCPD65" and k.suffix_map == {"color": "black", "region": "US"}
    k = parse_model("YK-HD12-US-W")          # 地域と色の順序が逆でも分離できる
    assert k.base == "YKHD12" and k.suffix_map == {"color": "white", "region": "US"}
    k = parse_model("KP-20K-BK")
    assert k.base == "KP" and k.suffix_map == {"capacity": "20K", "color": "black"}
    k = parse_model("TR-LB-56WH-JP")
    assert k.base == "TRLB" and k.suffix_map == {"capacity": "56WH", "region": "JP"}
    k = parse_model("HV-123-01")             # JP 型番の枝番
    assert k.base == "HV123" and k.suffix_map == {"branch": "01"}


def test_short_models_keep_head_tokens():
    # 「SE-20」の 20 は枝番ではない（先頭 2 トークンは残す）、「USB-C」の C はシアンではない
    assert parse_model("SE-20").base == "SE20"
    assert parse_model("USB-C").base == "USBC"
    assert parse_model("SE-20-BK").base == "SE20"


def test_compare_models_same_sibling_different():
    assert compare_models("KC-PD65-BK", "ｋｃ－ｐｄ６５－ｂｋ") is Relation.SAME
    assert compare_models("KC-PD65", "KC-PD65-BK") is Relation.SAME            # 部分指定
    assert compare_models("KC-PD65-BK", "KC-PD65-WH") is Relation.SIBLING_VARIANT
    assert compare_models("KP-10K-BK", "KP-20K-BK") is Relation.SIBLING_VARIANT
    assert compare_models("KC-PD65-BK", "KC-PD45-BK") is Relation.DIFFERENT     # 世代違いは別商品
    assert compare_models("HV-123-01", "HV-123-02") is Relation.SIBLING_VARIANT


# ───────────── GTIN ─────────────

def test_gtin_normalization_and_check_digit():
    body = "201020100000"
    ean = body + gtin_check_digit(body)
    assert normalize_gtin(ean) == "0" + ean
    assert normalize_gtin("0" + ean) == normalize_gtin(ean)                    # GTIN-14 表記
    assert normalize_gtin(" ".join([ean[:1], ean[1:7], ean[7:]])) == normalize_gtin(ean)
    assert normalize_gtin("２" + ean[1:]) == normalize_gtin(ean)               # 全角数字
    bad = ean[:-1] + str((int(ean[-1]) + 1) % 10)
    assert normalize_gtin(bad) is None
    assert normalize_gtin("12345") is None
    upc_body = "21020300000"
    upc = upc_body + gtin_check_digit(upc_body)
    assert normalize_gtin(upc) == normalize_gtin("0" + upc) == "00" + upc       # UPC-A = 0 付き EAN-13


def _catalog():
    def g(body):
        return body + gtin_check_digit(body)
    return {
        "a-bk": {"gtin": g("201020100000"), "model_number": "KC-PD65-BK", "brand": "Kestrelon", "family_id": "f65"},
        "a-wh": {"gtin": g("201020200000"), "model_number": "KC-PD65-WH", "brand": "Kestrelon", "family_id": "f65"},
        "a-us": {"gtin": g("21020300000"), "model_number": "KC-PD65-BK-US", "brand": "Kestrelon", "family_id": "f65"},
        "b-wh": {"gtin": g("201010100000"), "model_number": "KC-PD45-WH", "brand": "Kestrelon", "family_id": "f45"},
        "food": {"gtin": g("202010100000"), "model_number": None, "brand": "Mizunagi", "family_id": "fs"},
    }


def test_index_resolution_by_gtin_and_model():
    cat = _catalog()
    idx = IdentityIndex(cat)
    assert idx.resolve(gtin=cat["food"]["gtin"]).product_id == "food"
    assert idx.resolve(model="kc pd65 bk").product_id == "a-bk"                 # 地域サフィックス無し = 完全一致優先
    assert idx.resolve(model="KC-PD65-BK-US").product_id == "a-us"
    r = idx.resolve(model="KC-PD65")
    assert r.status is ResolveStatus.AMBIGUOUS and set(r.candidates) == {"a-bk", "a-wh", "a-us"}
    assert idx.resolve(model="KC-PD65-RD").status is ResolveStatus.NOT_FOUND     # 存在しない色
    assert idx.resolve(model="ZZ-999").status is ResolveStatus.NOT_FOUND
    r = idx.resolve(gtin=cat["a-bk"]["gtin"], model="KC-PD45-WH")
    assert r.status is ResolveStatus.CONFLICT and r.product_id == "a-bk"          # GTIN が第一キー
    r = idx.resolve(gtin="2010201000009", model="KC-PD65-WH")                    # 不正 GTIN → 型番で解決
    assert r.product_id == "a-wh" and any("invalid GTIN" in n for n in r.notes)


def test_catalog_relation():
    idx = IdentityIndex(_catalog())
    assert idx.relation("a-bk", "a-bk") is Relation.SAME
    assert idx.relation("a-bk", "a-wh") is Relation.SIBLING_VARIANT
    assert idx.relation("a-bk", "a-us") is Relation.SIBLING_VARIANT              # 地域版も兄弟
    assert idx.relation("a-bk", "b-wh") is Relation.DIFFERENT


def test_duplicate_gtin_rejected():
    cat = _catalog()
    cat["dup"] = dict(cat["a-bk"], model_number="XX-1")
    with pytest.raises(ValueError):
        IdentityIndex(cat)


# ───────────── 単位・許容誤差 ─────────────

def test_unit_conversion_to_si():
    u = units()
    assert u.quantity(6.1, "inch").si() == pytest.approx(0.15494)
    assert u.quantity(6.1, "インチ").si() == pytest.approx(0.15494)
    assert u.quantity(12, "oz").si() == pytest.approx(0.340194, rel=1e-5)
    assert u.quantity(1.4, "fl oz").si() == pytest.approx(4.14029e-5, rel=1e-5)
    assert u.quantity(1.4, "oz", prefer_dimension="volume").dimension == "volume"   # 曖昧単位の読み替え
    assert u.quantity(20000, "mAh").si() == pytest.approx(72000)
    assert u.quantity(56, "Wh").si() == pytest.approx(201600)
    assert u.quantity(150, "ｍｌ").si() == pytest.approx(1.5e-4)
    assert u.quantity(8, "時間").si() == 8 * 3600
    assert u.convert(u.quantity(2, "m"), "ft") == pytest.approx(6.5617, rel=1e-4)


def test_unknown_unit_raises():
    from src.normalize import UnitError
    with pytest.raises(UnitError):
        units().quantity(1, "furlongs")


def test_parse_quantity():
    assert parse_quantity("65W") == (65.0, "W")
    assert parse_quantity("20,000 mAh") == (20000.0, "mAh")
    assert parse_quantity("６．１インチ") == (6.1, "インチ")
    assert parse_quantity("black") is None


def test_tolerance_boundaries_inclusive():
    t = tolerances()
    u = units()
    # 既定の長さ: max(0.5 mm, 1%)。gold 40 mm → tol 0.5 mm（1% = 0.4 mm より大きい）
    gold = u.quantity(40, "mm").si()
    tol = t.tolerance_si("electronics.charger", "width", "length", gold)
    assert tol == pytest.approx(0.0005)
    assert t.within(u.quantity(40.5, "mm").si(), gold, tol)          # 境界ちょうどは一致
    assert not t.within(u.quantity(40.51, "mm").si(), gold, tol)
    # ケーブル長はカテゴリ上書きで ±10 mm、相対 0
    gold = u.quantity(2, "m").si()
    tol = t.tolerance_si("electronics.cable", "length", "length", gold)
    assert t.within(u.quantity(2.01, "m").si(), gold, tol)
    assert not t.within(u.quantity(2.011, "m").si(), gold, tol)
    # 定格出力は厳密一致
    gold = u.quantity(65, "W").si()
    tol = t.tolerance_si("electronics.charger", "output_power", "power", gold)
    assert t.within(gold, gold, tol) and not t.within(u.quantity(64.9, "W").si(), gold, tol)
    # 食品内容量: oz↔g の丸めのみ (±0.5 g)。12 oz = 340.19 g
    gold = u.quantity(12, "oz").si()
    tol = t.tolerance_si("food.cereal", "net_weight", "mass", gold)
    assert t.within(u.quantity(340, "g").si(), gold, tol)
    assert not t.within(u.quantity(339.6, "g").si(), gold, tol)
    # 属性上書き（電池持ち ±10%）、カテゴリ属性上書き（食品のカフェイン ±1 mg）
    gold = u.quantity(8, "h").si()
    assert t.within(u.quantity(8.8, "h").si(), gold, t.tolerance_si("electronics.earbuds", "battery_life", "time", gold))
    gold = u.quantity(80, "mg").si()
    tol = t.tolerance_si("food.beverage", "caffeine", "mass", gold)
    assert t.within(u.quantity(81, "mg").si(), gold, tol) and not t.within(u.quantity(81.5, "mg").si(), gold, tol)


def test_shoe_size_table():
    assert units().shoe_size_cm("shoe_mens", "US 9") == 27.0
    assert units().shoe_size_cm("shoe_mens", "US 9.0") == 27.0
    assert units().shoe_size_cm("shoe_mens", "27.0cm") == 27.0
    assert units().shoe_size_cm("shoe_mens", "US 20") is None


# ───────────── 語彙 ─────────────

def test_vocab_bilingual():
    v = vocab()
    assert v.color("ブラック") == v.color("BK") == v.color("black") == "black"
    assert v.ingredient("全粉乳").classes == {"milk"}
    assert v.ingredient("乳成分").is_class and v.ingredient("乳成分").key == "milk"
    assert v.ingredient("乳化剤（大豆由来）").classes == {"soy"}               # 「乳」を含むが乳ではない
    assert v.ingredient("glycerin").key == v.ingredient("グリセリン").key
    assert not v.ingredient("謎の成分X").recognized
    assert v.compat_key("ミズナギ PX-810") == v.compat_key("mizunagi px810")
    assert v.compat_key("USB Power Delivery 3.0") == v.compat_key("USB PD 3.0")
    assert v.compat_key("Ostrel BookAir 14 (2024)") != v.compat_key("Ostrel BookAir 14 (2025)")
    assert text_key("イヤーピース (S/M/L)") == text_key("イヤーピース(s/m/l)")
