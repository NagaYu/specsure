"""Phase 2 Step 3: 自由文 → claim 抽出 v1（評価セットの文ではなく、ルールごとの最小ケース）"""

from src.extract import extract, split_sentences


def kinds(res):
    return [(e.claim.claim_type, e.claim.attribute, e.claim.value, e.claim.unit, e.claim.polarity) for e in res.claims]


def test_sentence_split_and_spans():
    text = "重量は250gです。It weighs 9 oz. Great!"
    sents = split_sentences(text)
    assert [s.text for s in sents] == ["重量は250gです。", "It weighs 9 oz.", "Great!"]
    res = extract(text, {"gtin": "x"})
    for e in res.claims:
        assert text[e.span.start:e.span.end] == e.span.text and e.claim.product_ref == {"gtin": "x"}
        assert e.claim.provenance["generator"].startswith("extract-rules/")
    assert res.covered == [True, True, False]


def test_numeric_requires_cue_or_unambiguous_unit():
    assert kinds(extract("出力は30Wです。")) == [("numeric", "output_power", 30, "W", "affirm")]
    assert extract("30Wです。").claims == []                     # 手がかり語なしの W は拾わない（precision 優先）
    assert kinds(extract("5000mAh。"))[0][1] == "capacity_charge"   # mAh は単位だけで属性が決まる
    assert kinds(extract("Net weight is 12 oz."))[0][1] == "net_weight"  # 長い手がかり語を優先
    assert kinds(extract("電圧はAC100Vです。")) == [("region", "voltage", 100, "V", "affirm")]


def test_numbers_inside_model_numbers_are_ignored():
    assert extract("型番はKP-20K-BKです。").claims[0].claim.claim_type == "model"
    assert [k for k in kinds(extract("The TR-LB-56WH works well.")) if k[0] == "numeric"] == []


def test_compat_affirm_deny_and_lists():
    ks = kinds(extract("このインクはPX-810、PX-820に対応しています。PX-710には対応していません。"))
    assert ("compatibility", "compatible_with", "PX-810", None, "affirm") in ks
    assert ("compatibility", "compatible_with", "PX-710", None, "deny") in ks
    ks = kinds(extract("It is not compatible with the Nova 4, but works with the Nova 5 and Nova 5 Pro."))
    assert ("compatibility", "compatible_with", "Nova 4", None, "deny") in ks
    assert {k[2] for k in ks if k[4] == "affirm"} == {"Nova 5", "Nova 5 Pro"}


def test_ingredient_presence_absence():
    ks = kinds(extract("このクッキーは卵と小麦を含みます。乳成分不使用です。"))
    assert ("ingredient", "contains", "卵", None, "affirm") in ks
    assert ("ingredient", "contains", "乳成分", None, "deny") in ks
    ks = kinds(extract("It is gluten-free and contains oats."))
    assert ("ingredient", "contains", "gluten", None, "deny") in ks and ("ingredient", "contains", "oats", None, "affirm") in ks
    assert ("ingredient", "contains", "シリコン", None, "deny") in kinds(extract("ノンシリコンです。"))


def test_bundle():
    ks = kinds(extract("取扱説明書が付属します。ACアダプターは別売です。"))
    assert ("bundle", "includes", "取扱説明書", None, "affirm") in ks and ("bundle", "includes", "ACアダプター", None, "deny") in ks
    ks = kinds(extract("It comes with a pouch, but the charger is sold separately."))
    assert ("bundle", "includes", "pouch", None, "affirm") in ks and ("bundle", "includes", "charger", None, "deny") in ks


def test_hedges_and_opinions_are_not_claims():
    assert extract("おすすめです！軽くて便利だと思います。").claims == []
    assert extract("I think it might work with most phones.").claims == []


# ───────────── extractor v2（Phase 3 Step 2）: 評価セットに無い文で各ルールを固定 ─────────────

def test_v2_negated_free_claims_flip_to_affirm():
    ks = kinds(extract("Sadly this cookie isn't gluten-free."))
    assert ks == [("ingredient", "contains", "gluten", None, "affirm")]
    assert kinds(extract("This one is egg-free."))[0][4] == "deny"
    assert kinds(extract("この石けんはパラベンフリーではありません。"))[0][4] == "affirm"


def test_v2_negated_numbers_are_not_claims():
    assert extract("北米の120V環境では使えません。").claims == []
    assert extract("It does not run on 240 V.").claims == []
    assert [k[1] for k in kinds(extract("電圧は100Vです。"))] == ["voltage"]


def test_v2_header_lists_and_paren_aware_split():
    ks = kinds(extract("同梱物：本体、電源コード、ブラシ（大/小）。"))
    assert [k[2] for k in ks] == ["本体", "電源コード", "ブラシ(大/小)"] and all(k[0] == "bundle" for k in ks)
    ks = kinds(extract("Compatible models: Alpha 3 / Alpha 4."))
    assert [k[2] for k in ks] == ["Alpha 3", "Alpha 4"] and all(k[0] == "compatibility" for k in ks)


def test_v2_compat_dedicated_and_made_for():
    assert ("compatibility", "compatible_with", "Zeta X2", None, "affirm") in kinds(extract("このカバーはZeta X2専用です。"))
    assert kinds(extract("It is made for the Zeta X2."))[0][2] == "Zeta X2"
    assert kinds(extract("It won't fit the Zeta X1."))[0][4] == "deny"


def test_v2_ingredient_list_negation_and_bare_haigo():
    ks = kinds(extract("着色料・保存料は入っていません。"))
    assert {k[2] for k in ks} == {"着色料", "保存料"} and all(k[4] == "deny" for k in ks)
    assert ("ingredient", "contains", "セラミド", None, "affirm") in kinds(extract("セラミド配合のクリームです。"))


def test_v2_catalog_dimension_fallback(bench):
    p = bench.products["jp-yk-hd12-w"]                      # ドライヤー: power の属性は power_consumption のみ
    res = extract("パワフルな1200Wです。", {"model_number": p["model_number"]}, catalog=bench)
    assert kinds(res) == [("numeric", "power_consumption", 1200, "W", "affirm")]
    assert extract("パワフルな1200Wです。").claims == []        # カタログなしでは割り当てない
