"""gold から既知ラベルの誤り claim を決定的に生成する（verifier の評価セット）。

すべての mutation claim は label.expected = FAIL、label.error_type（E1–E8）、
label.rationale（どの gold 値とどう違うか: method / gold_path / gold_value / mutated_value / source_product_id）を持つ。
候補の段階で「対象商品の gold スナップショットだけで誤りと断定できるか（evidence）」を計算し、
断定できない候補（網羅的でない互換性リスト外の機種、主要成分しか無い商品への成分追加など）は捨てる。
それらは正解ラベル自体が立たないため mutation ではなく、claims.probe_claims（正解 UNKNOWN）で扱う。

E6（価格・在庫の陳腐化）は時刻依存のため Phase 1 では生成しない。
"""

from __future__ import annotations

import random
from typing import Any, Callable, Iterator

from .claims import Claim, compat_surface, product_ref_variant, severity_for, term_surface
from .normalize import is_number, precision_floor_si, text_key, tolerances, units, vocab

GENERATOR = "mutate/v1"
ERROR_TYPES = ("E1", "E2", "E3", "E4", "E5", "E7", "E8")
SHIFT_FACTORS = (1.25, 0.8, 1.5, 2.0, 0.5)
BUNDLE_DISTRACTORS = ("ac_adapter", "carrying_case", "wireless_pad")
PACK_ATTRS = ("pack_count",)


Candidate = dict  # {claim_type, value, attribute, unit, polarity, rationale, evidence}


def _numeric_attrs(snap: dict) -> dict[str, dict]:
    return {k: v for k, v in (snap.get("attributes") or {}).items() if is_number(v.get("value"))}


def _differs(category: str, attribute: str, gold: dict, value: float, unit: str) -> bool:
    """mutated 値が gold と許容誤差を超えて異なるか（= 正解ラベル FAIL が妥当か）。"""
    u = units()
    g = u.quantity(gold["value"], gold.get("unit", ""))
    try:
        q = u.quantity(value, unit, g.dimension)
    except Exception:
        return False
    if q.dimension != g.dimension:
        return True
    # verifier と同じく表記精度フロア（主張値の最終桁の半分）を許容に含める: 13.5 fl oz は 400 mL として真
    tol = max(tolerances().tolerance_si(category, attribute, g.dimension, g.si()),
              precision_floor_si(value, q.unit, q.dimension))
    return not tolerances().within(q.si(), g.si(), tol)


def _ingredient_names(ing: dict, with_subs: bool = True) -> list[str]:
    out = []
    for i in ing.get("items", []):
        if isinstance(i, str):
            out.append(i)
        else:
            out.append(i["name"])
            if with_subs:
                out.extend(i.get("contains", []))
    return out


def _ingredient_keys(ing: dict) -> tuple[set[str], set[str], bool]:
    """(成分キー, 含有クラス, 全成分が語彙で認識できるか)"""
    v = vocab()
    keys, classes, recognized = set(), set(), True
    for n in _ingredient_names(ing) + list(ing.get("allergens", [])):
        r = v.ingredient(n)
        keys.add(r.key)
        classes |= set(r.classes)
        recognized &= r.recognized
    return keys, classes, recognized


def _absence_is_evidenced(ing: dict) -> bool:
    return bool(ing.get("exhaustive")) and _ingredient_keys(ing)[2]


def _compat_keys(comp: dict | None) -> tuple[set[str], set[str]]:
    v = vocab()
    comp = comp or {}
    return ({v.compat_key(i) for i in comp.get("compatible", [])},
            {v.compat_key(i) for i in comp.get("incompatible", [])})


def _cand(ctype, value, rationale, *, attribute=None, unit=None, polarity="affirm", evidence=True) -> Candidate:
    return {"claim_type": ctype, "value": value, "attribute": attribute, "unit": unit, "polarity": polarity,
            "rationale": rationale, "evidence": evidence}


# ───────────────────────────── 類型ごとの候補生成 ─────────────────────────────

def _swap_values(cat, pid: str, other: str, method: str) -> Iterator[Candidate]:
    """他商品（世代・兄弟・地域版）の値を対象商品の値として言う候補"""
    p, s = cat.products[pid], cat.snapshots[pid]
    o, so = cat.products[other], cat.snapshots[other]
    mine, theirs = _numeric_attrs(s), _numeric_attrs(so)
    for attr in sorted(set(mine) & set(theirs)):
        t = theirs[attr]
        if _differs(p["category"], attr, mine[attr], t["value"], t.get("unit", "")):
            yield _cand("numeric", t["value"], {
                "method": f"{method}_numeric", "gold_path": f"attributes.{attr}", "gold_value": mine[attr],
                "mutated_value": t, "source_product_id": other}, attribute=attr, unit=t.get("unit"))
    if p.get("model_number") and o.get("model_number"):
        yield _cand("model", o["model_number"], {
            "method": f"{method}_model", "gold_path": "product.model_number", "gold_value": p["model_number"],
            "mutated_value": o["model_number"], "source_product_id": other})
    comp = s.get("compatibility")
    if comp and so.get("compatibility"):
        compat, incompat = _compat_keys(comp)
        for item in so["compatibility"].get("compatible", []):
            k = vocab().compat_key(item)
            if k in compat:
                continue
            yield _cand("compatibility", item, {
                "method": f"{method}_compat", "gold_path": "compatibility", "gold_value": comp,
                "mutated_value": item, "source_product_id": other},
                attribute="compatible_with", evidence=bool(comp.get("exhaustive")) or k in incompat)
    ing, ingo = s.get("ingredients"), so.get("ingredients")
    if ing and ingo:
        keys, classes, _ = _ingredient_keys(ing)
        okeys, _, _ = _ingredient_keys(ingo)
        for n in _ingredient_names(ingo, with_subs=False):
            r = vocab().ingredient(n)
            if r.key in keys or (r.classes & classes):
                continue
            yield _cand("ingredient", n, {
                "method": f"{method}_ingredient_add", "gold_path": "ingredients.items", "gold_value": ing["items"],
                "mutated_value": n, "source_product_id": other},
                attribute="contains", evidence=_absence_is_evidenced(ing))
        for n in _ingredient_names(ing, with_subs=False):
            r = vocab().ingredient(n)
            if r.key not in okeys and r.recognized:
                yield _cand("ingredient", n, {
                    "method": f"{method}_ingredient_deny", "gold_path": "ingredients.items", "gold_value": ing["items"],
                    "mutated_value": f"not {n}", "source_product_id": other},
                    attribute="contains", polarity="deny")


def gen_e1(cat, pid) -> Iterator[Candidate]:
    for other in cat.generation_neighbors(pid):
        yield from _swap_values(cat, pid, other, "swap_generation")


def gen_e2(cat, pid) -> Iterator[Candidate]:
    p = cat.products[pid]
    for other in cat.variant_siblings(pid):
        o = cat.products[other]
        for axis, val in sorted((p.get("variant_axes") or {}).items()):
            ov = (o.get("variant_axes") or {}).get(axis)
            if ov is not None and vocab().term(ov) != vocab().term(val):
                yield _cand("variant", ov, {
                    "method": "swap_variant_axis", "gold_path": f"variant_axes.{axis}", "gold_value": val,
                    "mutated_value": ov, "source_product_id": other}, attribute=axis)
        yield from _swap_values(cat, pid, other, "swap_variant")


def gen_e3(cat, pid) -> Iterator[Candidate]:
    p, s = cat.products[pid], cat.snapshots[pid]
    u = units()
    for attr, gold in sorted(_numeric_attrs(s).items()):
        if attr in PACK_ATTRS:                 # 入数の誤りは E5（gen_e5）で扱う
            continue
        unit = gold.get("unit", "")
        q = u.quantity(gold["value"], unit)
        for f in SHIFT_FACTORS:
            val = round(gold["value"] * f) if q.dimension == "count" else round(gold["value"] * f, 2)
            val = int(val) if float(val).is_integer() else val
            if _differs(p["category"], attr, gold, val, unit):
                yield _cand("numeric", val, {
                    "method": "shift_value", "factor": f, "gold_path": f"attributes.{attr}", "gold_value": gold,
                    "mutated_value": {"value": val, "unit": unit}}, attribute=attr, unit=unit)
                break
        for alt in u.confusable.get(q.dimension, []):
            if alt == q.unit:
                continue
            if _differs(p["category"], attr, gold, gold["value"], alt):
                yield _cand("numeric", gold["value"], {
                    "method": "unit_swap", "gold_path": f"attributes.{attr}", "gold_value": gold,
                    "mutated_value": {"value": gold["value"], "unit": alt}}, attribute=attr, unit=alt)
                break


def gen_e4(cat, pid) -> Iterator[Candidate]:
    p, s = cat.products[pid], cat.snapshots[pid]
    comp = s.get("compatibility")
    if not comp:
        return
    v = vocab()
    compat, incompat = _compat_keys(comp)
    for item in comp.get("incompatible", []):
        yield _cand("compatibility", item, {
            "method": "affirm_listed_incompatible", "gold_path": "compatibility.incompatible",
            "gold_value": comp, "mutated_value": item}, attribute="compatible_with")
    for item in comp.get("compatible", []):
        yield _cand("compatibility", item, {
            "method": "deny_listed_compatible", "gold_path": "compatibility.compatible",
            "gold_value": comp, "mutated_value": f"not {item}"}, attribute="compatible_with", polarity="deny")
    for q in sorted(cat.products):
        if q == pid or cat.products[q]["category"] != p["category"] or not cat.snapshots[q].get("compatibility"):
            continue
        for item in cat.snapshots[q]["compatibility"].get("compatible", []):
            k = v.compat_key(item)
            if k in compat or k in incompat:
                continue
            yield _cand("compatibility", item, {
                "method": "affirm_unlisted_same_category", "gold_path": "compatibility", "gold_value": comp,
                "mutated_value": item, "source_product_id": q},
                attribute="compatible_with", evidence=bool(comp.get("exhaustive")))


def gen_e5(cat, pid) -> Iterator[Candidate]:
    p, s = cat.products[pid], cat.snapshots[pid]
    for attr in PACK_ATTRS:                      # E5 数量: 入数を取り違える（倍・半分・±1）
        gold = _numeric_attrs(s).get(attr)
        if not gold:
            continue
        n = int(gold["value"])
        for val in dict.fromkeys(v for v in (n * 2, n // 2, n + 1, n - 1) if v > 0 and v != n):
            yield _cand("numeric", val, {"method": "swap_pack_count", "gold_path": f"attributes.{attr}",
                                         "gold_value": gold, "mutated_value": {"value": val, "unit": gold.get("unit")}},
                        attribute=attr, unit=gold.get("unit"))
    b = s.get("bundle")
    if not b:
        return
    v = vocab()
    have = {v.term(i) for i in b["items"]}
    for item in b["items"]:
        yield _cand("bundle", item, {"method": "deny_included_item", "gold_path": "bundle.items",
                                     "gold_value": b["items"], "mutated_value": f"not {item}"},
                    attribute="includes", polarity="deny")
    pool = []
    for q in cat.family(pid) + cat.generation_neighbors(pid):
        pool.extend((q, i) for i in (cat.snapshots[q].get("bundle") or {}).get("items", []))
    names = v.cfg["terms"]
    pool.extend((None, names[t][0 if p["region"] == "JP" else 1]) for t in BUNDLE_DISTRACTORS)
    for q, item in pool:
        if v.term(item) in have:
            continue
        yield _cand("bundle", item, {"method": "affirm_absent_item", "gold_path": "bundle.items",
                                     "gold_value": b["items"], "mutated_value": item, "source_product_id": q},
                    attribute="includes", evidence=bool(b.get("exhaustive")))


def gen_e7(cat, pid) -> Iterator[Candidate]:
    p, s = cat.products[pid], cat.snapshots[pid]
    ing = s.get("ingredients")
    if not ing:
        return
    v = vocab()
    keys, classes, _ = _ingredient_keys(ing)
    for a in ing.get("allergens", []):
        yield _cand("ingredient", a, {"method": "deny_declared_allergen", "gold_path": "ingredients.allergens",
                                      "gold_value": ing.get("allergens"), "mutated_value": f"{a}-free"},
                    attribute="contains", polarity="deny")
    for n in _ingredient_names(ing, with_subs=False):
        if v.ingredient(n).recognized:
            yield _cand("ingredient", n, {"method": "deny_listed_ingredient", "gold_path": "ingredients.items",
                                          "gold_value": ing["items"], "mutated_value": f"not {n}"},
                        attribute="contains", polarity="deny")
    for a in ing.get("may_contain", []):
        yield _cand("ingredient", a, {"method": "deny_may_contain", "gold_path": "ingredients.may_contain",
                                      "gold_value": ing.get("may_contain"), "mutated_value": f"{a}-free",
                                      "note": "表示に「含む可能性」があるのに不使用と言う"},
                    attribute="contains", polarity="deny")
    top = p["category"].split(".")[0]
    for q in sorted(cat.products):
        if q == pid or cat.products[q]["category"].split(".")[0] != top:
            continue
        other = cat.snapshots[q].get("ingredients")
        if not other:
            continue
        for n in _ingredient_names(other, with_subs=False):
            r = v.ingredient(n)
            if not r.recognized or r.key in keys or (r.classes & classes):
                continue
            yield _cand("ingredient", n, {"method": "add_absent_ingredient", "gold_path": "ingredients.items",
                                          "gold_value": ing["items"], "mutated_value": n, "source_product_id": q},
                        attribute="contains", evidence=_absence_is_evidenced(ing))


def gen_e8(cat, pid) -> Iterator[Candidate]:
    s = cat.snapshots[pid]
    mine = s.get("region_spec") or {}
    for other in cat.region_siblings(pid):
        theirs = cat.snapshots[other].get("region_spec") or {}
        for attr in sorted(set(mine) & set(theirs)):
            a, b = mine[attr], theirs[attr]
            if a == b:
                continue
            if "value" in b and is_number(b["value"]) and "value" in a and is_number(a["value"]):
                if not _differs(cat.products[pid]["category"], attr, a, b["value"], b.get("unit", "")):
                    continue
            if "min" in b or "min" in a:
                continue
            yield _cand("region", b["value"], {
                "method": "swap_region_spec", "gold_path": f"region_spec.{attr}", "gold_value": a,
                "mutated_value": b, "source_product_id": other}, attribute=attr, unit=b.get("unit"))
        for c in _swap_values(cat, pid, other, "swap_region"):
            if c["claim_type"] != "model":       # 地域サフィックス違いの型番は E1/E2 で扱う
                yield c


GENERATORS: dict[str, Callable] = {"E1": gen_e1, "E2": gen_e2, "E3": gen_e3, "E4": gen_e4, "E5": gen_e5,
                                   "E7": gen_e7, "E8": gen_e8}


# ───────────────────────────── 組み立て ─────────────────────────────

def _surface(c: Candidate, key: str) -> Any:
    if c["claim_type"] == "ingredient":
        return term_surface(c["value"], "ingredient", key)
    if c["claim_type"] == "bundle":
        return term_surface(c["value"], "term", key)
    if c["claim_type"] == "compatibility":
        return compat_surface(c["value"], key)
    return c["value"]


def mutations(catalog, seed: int = 0, per_type: int = 2) -> list[Claim]:
    """seed 固定で決定的。各商品×各類型から最大 per_type 件。"""
    rng = random.Random(seed)
    out: list[Claim] = []
    for pid in sorted(catalog.products):
        product = catalog.products[pid]
        for etype in ERROR_TYPES:
            cands = [c for c in GENERATORS[etype](catalog, pid) if c["evidence"]]
            uniq, seen = [], set()
            for c in cands:
                k = (c["claim_type"], c["attribute"], text_key(c["value"]), c["unit"], c["polarity"])
                if k not in seen:
                    seen.add(k)
                    uniq.append(c)
            picked = rng.sample(uniq, min(per_type, len(uniq)))
            for i, c in enumerate(picked):
                cid = f"m-{pid}-{etype}-{i}"
                out.append(Claim(
                    claim_id=cid, claim_type=c["claim_type"], value=_surface(c, cid), attribute=c["attribute"],
                    unit=c["unit"], polarity=c["polarity"], product_ref=product_ref_variant(product, cid),
                    product_id=pid, severity=severity_for(c["claim_type"], c["attribute"]),
                    provenance={"kind": "mutation", "generator": GENERATOR, "seed": seed},
                    label={"expected": "FAIL", "error_type": etype, "rationale": c["rationale"],
                           "evidence_in_snapshot": bool(c["evidence"])},
                ))
    return out
