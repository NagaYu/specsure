"""Claim のスキーマと、gold（正しい）claim の決定的な生成。

Claim は「エージェントが言った（言いうる）一つの主張」。verifier が見てよいのは verifier_view() の範囲だけで、
product_id（gold の紐付け）と label（正解）は評価専用。

product_ref はエージェントが実際に口にする識別子（GTIN/JAN・型番）で、表記揺れを意図的に混ぜる。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .normalize import is_number, load_config, nfkc, text_key, tolerances, units, vocab

CLAIM_TYPES = ("numeric", "model", "compatibility", "ingredient", "bundle", "variant", "region", "price")
POLARITIES = ("affirm", "deny")
SEVERITIES = ("S1", "S2", "S3")
VERDICTS = ("PASS", "FAIL", "UNKNOWN")
PROVENANCE_KINDS = ("gold", "mutation", "probe", "agent")


@dataclass
class Claim:
    claim_id: str
    claim_type: str
    value: Any
    product_ref: dict = field(default_factory=dict)      # {gtin?, model_number?}
    attribute: str | None = None
    unit: str | None = None
    polarity: str = "affirm"
    severity: str | None = None                          # 重大度の前提カテゴリ（taxonomy から付与）
    product_id: str | None = None                        # gold の紐付け（評価専用、verifier は見ない）
    provenance: dict = field(default_factory=dict)       # {kind, record_id?, generator?}
    label: dict | None = None                            # {expected, error_type, rationale, evidence_in_snapshot}

    def __post_init__(self):
        validate(self)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Claim":
        kw = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        for k in ("product_ref", "provenance"):
            kw[k] = kw.get(k) or {}
        return cls(**kw)

    def verifier_view(self) -> dict:
        """verifier に渡す入力。正解ラベルと gold の紐付けは落とす。"""
        return {k: v for k, v in self.to_dict().items() if k not in ("label", "product_id", "provenance")}


class ClaimError(ValueError):
    pass


def validate(c: Claim) -> None:
    if not c.claim_id:
        raise ClaimError("claim_id required")
    if c.claim_type not in CLAIM_TYPES:
        raise ClaimError(f"{c.claim_id}: claim_type {c.claim_type!r} not in {CLAIM_TYPES}")
    if c.polarity not in POLARITIES:
        raise ClaimError(f"{c.claim_id}: polarity {c.polarity!r}")
    if c.severity is not None and c.severity not in SEVERITIES:
        raise ClaimError(f"{c.claim_id}: severity {c.severity!r}")
    if c.claim_type == "numeric" and not (is_number(c.value) or isinstance(c.value, (dict, str))):
        raise ClaimError(f"{c.claim_id}: numeric claim needs a number, {{min,max}} or a quantity string")
    if c.claim_type in ("numeric", "region", "variant") and not c.attribute:
        raise ClaimError(f"{c.claim_id}: {c.claim_type} claim needs attribute")
    if c.provenance and c.provenance.get("kind") not in PROVENANCE_KINDS:
        raise ClaimError(f"{c.claim_id}: provenance.kind {c.provenance.get('kind')!r}")
    if c.label is not None:
        if c.label.get("expected") not in VERDICTS:
            raise ClaimError(f"{c.claim_id}: label.expected {c.label.get('expected')!r}")
        if c.label["expected"] == "FAIL" and not (c.label.get("error_type") and c.label.get("rationale")):
            raise ClaimError(f"{c.claim_id}: FAIL label needs error_type and rationale")


def severity_for(claim_type: str, attribute: str | None) -> str:
    rules = load_config("taxonomy.yaml")["severity_rules"]
    by_attr = rules.get("by_attribute") or {}
    if attribute in by_attr:
        return by_attr[attribute]
    return rules["by_claim_type"][claim_type]


def stricter(a: str | None, b: str | None) -> str:
    vals = [s for s in (a, b) if s]
    return min(vals) if vals else "S3"          # "S1" < "S2" < "S3"


def read_jsonl(path: Path) -> list[Claim]:
    return [Claim.from_dict(json.loads(l)) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def write_jsonl(claims: Iterable[Claim], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(c.to_dict(), ensure_ascii=False, sort_keys=True) + "\n" for c in claims),
                    encoding="utf-8")
    return path


# ───────────────────────────── 表記揺れ（決定的） ─────────────────────────────

def stable_int(*parts: Any) -> int:
    return int(hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:12], 16)


_FULLWIDTH = {ord(c): ord(c) + 0xFEE0 for c in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-"}
_FULLWIDTH[ord("-")] = ord("－")


def product_ref_variant(product: dict, key: str) -> dict:
    """エージェントが言いそうな識別子の表記を、claim ごとに決定的に選ぶ。"""
    gtin, model = product.get("gtin"), product.get("model_number")
    opts = [
        {"gtin": gtin},
        {"gtin": product["gtin14"]},
        {"gtin": f"{gtin[:1]} {gtin[1:7]} {gtin[7:]}"},
    ] if gtin else []
    if model:
        opts += [
            {"model_number": model},
            {"model_number": model.translate(_FULLWIDTH)},
            {"model_number": model.lower().replace("-", " ")},
            {"model_number": f"{product['brand']} {model}"},
        ] + ([{"gtin": gtin, "model_number": model}] if gtin else [])
    if not opts and product.get("product_name"):
        opts = [{"name": product["product_name"]}]
    return dict(opts[stable_int(key, "ref") % len(opts)])


# 換算した表記で言い換える先（次元 → 単位 → 言い換え候補）
_ALT_UNITS = {
    "length": {"mm": ["cm", "in"], "cm": ["mm"], "m": ["cm", "ft"], "in": ["mm", "cm"]},
    "mass": {"g": ["kg", "oz", "グラム"], "oz": ["g"], "mg": ["g"], "kg": ["g"]},
    "volume": {"ml": ["l", "fl oz", "ミリリットル"], "fl_oz": ["ml"]},
    "electric_charge": {"mah": ["ah"]},
    "data_rate": {"gbps": ["mbps"]},
    "time": {"h": ["min", "時間"]},
    "power": {"w": ["kw", "ワット"]},
}


def numeric_surface(value: float, unit: str, category: str, attribute: str, key: str) -> tuple[float, str]:
    """同じ量を別の単位で言い換える（gold を真のまま保つため、許容誤差の半分以内に収まる場合のみ）。"""
    u = units()
    q = u.quantity(value, unit)
    alts = _ALT_UNITS.get(q.dimension, {}).get(q.unit, [])
    choice = stable_int(key, "unit") % (len(alts) + 1)
    if choice == len(alts):
        return value, unit
    alt = alts[choice]
    target = alt
    conv = u.convert(q, target)
    for nd in (0, 1, 2, 3):
        cand = round(conv, nd)
        if cand == 0:
            continue
        gold_si = q.si()
        tol = tolerances().tolerance_si(category, attribute, q.dimension, gold_si)
        cand_si = u.quantity(cand, target, q.dimension).si()
        if abs(cand_si - gold_si) <= tol * 0.5 + 1e-12:
            return (int(cand) if float(cand).is_integer() else cand), alt
    return value, unit


def term_surface(text: str, kind: str, key: str) -> str:
    """色・汎用語・成分名を、語彙の別表記（日英）で言い換える。"""
    v = vocab()
    table = {"color": v.cfg.get("colors") or {}, "term": v.cfg.get("terms") or {}}.get(kind)
    if kind == "ingredient":
        ref = v.ingredient(text)
        if ref.is_class:
            names = (v.cfg["ingredient_classes"].get(ref.key) or [])
        elif ref.recognized:
            names = v.cfg["ingredients"][ref.key]["names"]
        else:
            names = []
    else:
        canon = v.color(text) if kind == "color" else v.terms.get(text_key(text))
        names = list((table or {}).get(canon) or []) if canon else []
        names = [n for n in names if len(text_key(n)) > 2]       # 1–2 文字の色コードは言い換えに使わない
    names = [n for n in names if text_key(n) != text_key(text)]
    if not names:
        return text
    i = stable_int(key, "term") % (len(names) + 1)
    return text if i == len(names) else names[i]


def compat_surface(item: str, key: str) -> str:
    v = vocab()
    out = item
    i = stable_int(key, "compat") % 3
    if i == 1:
        for brand, aliases in (v.cfg.get("brands") or {}).items():
            if out.startswith(brand) and aliases:
                out = aliases[0] + out[len(brand):]
                break
    elif i == 2:
        out = nfkc(out).lower()
    return out


# ───────────────────────────── gold claim 生成 ─────────────────────────────

def _mk(pid: str, product: dict, idx: int, ctype: str, value: Any, *, attribute=None, unit=None,
        polarity="affirm", rationale=None) -> Claim:
    cid = f"g-{pid}-{idx:03d}"
    return Claim(
        claim_id=cid, claim_type=ctype, value=value, attribute=attribute, unit=unit, polarity=polarity,
        product_ref=product_ref_variant(product, cid), product_id=pid,
        severity=severity_for(ctype, attribute),
        provenance={"kind": "gold", "generator": "claims.gold_claims/v1"},
        label={"expected": "PASS", "error_type": None, "rationale": rationale or {}, "evidence_in_snapshot": True},
    )


def gold_claims_for(product: dict, snap: dict, max_compat: int = 3, max_ingredients: int = 4) -> list[Claim]:
    pid = product["product_id"]
    cat = product["category"]
    out: list[Claim] = []

    def add(*a, **kw):
        out.append(_mk(pid, product, len(out), *a, **kw))

    for attr, v in sorted((snap.get("attributes") or {}).items()):
        if is_number(v.get("value")):
            val, unit = numeric_surface(v["value"], v.get("unit", ""), cat, attr, f"{pid}/{attr}")
            add("numeric", val, attribute=attr, unit=unit,
                rationale={"gold": f"attributes.{attr}", "gold_value": v})
    if product.get("model_number"):
        m = product["model_number"]
        base_only = "-".join(m.split("-")[:2])
        add("model", base_only if stable_int(pid, "model") % 2 else m,
            rationale={"gold": "product.model_number", "gold_value": m})
    for axis, val in sorted((product.get("variant_axes") or {}).items()):
        kind = "color" if axis == "color" else "term"
        add("variant", term_surface(str(val), kind, f"{pid}/{axis}"), attribute=axis,
            rationale={"gold": f"variant_axes.{axis}", "gold_value": val})
    for attr, v in sorted((snap.get("region_spec") or {}).items()):
        if "min" in v:
            add("region", {"min": v["min"], "max": v["max"]}, attribute=attr, unit=v.get("unit"),
                rationale={"gold": f"region_spec.{attr}", "gold_value": v})
            typical = {"JP": 100, "US": 120}.get(product["region"]) if attr == "voltage" else v["min"]
            if typical is not None and v["min"] <= typical <= v["max"]:
                add("region", typical, attribute=attr, unit=v.get("unit"),
                    rationale={"gold": f"region_spec.{attr}", "gold_value": v})
        elif is_number(v.get("value")):
            add("region", v["value"], attribute=attr, unit=v.get("unit"),
                rationale={"gold": f"region_spec.{attr}", "gold_value": v})
        else:
            add("region", v["value"], attribute=attr, rationale={"gold": f"region_spec.{attr}", "gold_value": v})
    comp = snap.get("compatibility")
    if comp:
        for item in comp.get("compatible", [])[:max_compat]:
            add("compatibility", compat_surface(item, f"{pid}/{item}"), attribute="compatible_with",
                rationale={"gold": "compatibility.compatible", "gold_value": item})
        for item in comp.get("incompatible", [])[:1]:
            add("compatibility", compat_surface(item, f"{pid}/{item}"), attribute="compatible_with", polarity="deny",
                rationale={"gold": "compatibility.incompatible", "gold_value": item})
    ing = snap.get("ingredients")
    if ing:
        for item in [i if isinstance(i, str) else i["name"] for i in ing["items"]][:max_ingredients]:
            add("ingredient", term_surface(item, "ingredient", f"{pid}/{item}"), attribute="contains",
                rationale={"gold": "ingredients.items", "gold_value": item})
        for a in ing.get("allergens", []):
            add("ingredient", term_surface(a, "ingredient", f"{pid}/allergen/{a}"), attribute="contains",
                rationale={"gold": "ingredients.allergens", "gold_value": a})
        if ing.get("exhaustive") and ing.get("allergen_statement"):
            present = _present_classes(ing)
            mandatory = sorted(vocab().mandatory_allergens.get(product["region"], ()))
            absent = [c for c in mandatory if c not in present]
            for c in absent[: 2]:
                add("ingredient", c, attribute="contains", polarity="deny",
                    rationale={"gold": "ingredients (exhaustive, allergen_statement)", "absent_class": c})
    bundle = snap.get("bundle")
    if bundle:
        for item in bundle["items"]:
            add("bundle", term_surface(item, "term", f"{pid}/bundle/{item}"), attribute="includes",
                rationale={"gold": "bundle.items", "gold_value": item})
    return out


def _present_classes(ing: dict) -> set[str]:
    v = vocab()
    present: set[str] = set()
    names = []
    for i in ing.get("items", []):
        if isinstance(i, str):
            names.append(i)
        else:
            names.append(i["name"])
            names.extend(i.get("contains", []))
    names += list(ing.get("allergens", [])) + list(ing.get("may_contain", []))
    for n in names:
        present |= set(v.ingredient(n).classes)
    return present


def gold_claims(catalog) -> list[Claim]:
    out = []
    for pid in sorted(catalog.products):
        out.extend(gold_claims_for(catalog.products[pid], catalog.snapshots[pid]))
    return out


# ───────────────────────────── 証拠なしプローブ（正解 UNKNOWN） ─────────────────────────────

def probe_claims(catalog) -> list[Claim]:
    """スナップショットからは判断できない主張。verifier は UNKNOWN を返すべき（推測しない）。"""
    from .identity import gtin_check_digit

    out: list[Claim] = []

    def add(pid, kind, ctype, value, ref, **kw):
        cid = f"p-{pid or 'none'}-{kind}"
        out.append(Claim(claim_id=cid, claim_type=ctype, value=value, product_ref=ref, product_id=pid,
                         severity=severity_for(ctype, kw.get("attribute")),
                         provenance={"kind": "probe", "generator": "claims.probe_claims/v1"},
                         label={"expected": "UNKNOWN", "error_type": None, "rationale": {"probe": kind},
                                "evidence_in_snapshot": False}, **kw))

    body = "209999900000"
    add(None, "unknown-gtin", "numeric", 65, {"gtin": body + gtin_check_digit(body)}, attribute="output_power", unit="W")
    add(None, "unknown-model", "numeric", 65, {"model_number": "ZX-9000-BK"}, attribute="output_power", unit="W")
    for pid in sorted(catalog.products):
        p, s = catalog.products[pid], catalog.snapshots[pid]
        ref = ({"gtin": p["gtin"]} if p.get("gtin") else {"model_number": p["model_number"]} if p.get("model_number")
               else {"name": p["product_name"]})
        if p.get("model_number"):
            parts = p["model_number"].split("-")
            for n in range(1, len(parts)):
                prefix = "-".join(parts[:n])
                if catalog.identity.resolve_model(prefix).status.value == "ambiguous":
                    add(pid, "ambiguous-model", "numeric", 1, {"model_number": prefix}, attribute="weight", unit="g")
                    break
        add(pid, "price", "price", 19.99, ref, attribute="price", unit="USD")
        if "battery_life" not in (s.get("attributes") or {}):
            add(pid, "missing-attr", "numeric", 10, ref, attribute="battery_life", unit="h")
        comp = s.get("compatibility")
        if comp and not comp.get("exhaustive"):
            add(pid, "compat-unlisted", "compatibility", "Ostrel BookPro 15 (2025)", ref, attribute="compatible_with")
        ing = s.get("ingredients")
        if ing and not ing.get("exhaustive"):
            add(pid, "ingredient-free-unverifiable", "ingredient", "milk", ref, attribute="contains", polarity="deny")
            add(pid, "ingredient-contains-unverifiable", "ingredient", "sesame", ref, attribute="contains")
    # 種別ごとにファミリー 1 件へ間引く（同じ型の probe で評価セットが膨らまないように）
    seen, uniq = set(), []
    for c in out:
        fam = catalog.products[c.product_id]["family_id"] if c.product_id else None
        key = (c.label["rationale"]["probe"], fam)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    return uniq


# ───────────────────────────── 手書きチャレンジセット ─────────────────────────────

CHALLENGE_V0 = "challenge_v0.yaml"
CHALLENGE_V1 = "challenge_v1.yaml"


def _challenge_ref(p: dict, style: str | None) -> dict:
    model = p.get("model_number")
    if style in (None, "gtin"):
        return {"gtin": p["gtin"]}
    if style == "gtin14":
        return {"gtin": p["gtin14"]}
    if style == "model":
        return {"model_number": model}
    if style == "model_lower":
        return {"model_number": model.lower().replace("-", " ")}
    if style == "model_fullwidth":
        return {"model_number": model.translate(_FULLWIDTH)}
    if style == "model_brand":
        return {"model_number": f"{p['brand']} {model}"}
    raise ValueError(f"unknown ref style {style!r}")


def challenge_catalog(path: Path | None = None):
    """チャレンジセットの評価カタログ（ベンチ seed + catalog_extension）"""
    import yaml

    from .normalize import ROOT
    from .snapshot import DEFAULT_SEED, Catalog, load_seed

    path = path or ROOT / "data" / "seed" / CHALLENGE_V0
    with open(path, encoding="utf-8") as f:
        ext = yaml.safe_load(f).get("catalog_extension")
    pairs = load_seed(DEFAULT_SEED) + (load_seed(ROOT / ext) if ext else [])
    return Catalog.from_pairs(pairs)


def challenge_claims(catalog, path: Path | None = None) -> list[Claim]:
    """手書きチャレンジセット（data/seed/challenge_v*.yaml）を Claim に変換する（語彙外の真の主張と現実的な誤り）。
    claim_id は v0 が "x-<id>"、v1 以降が "<version>-<id>"（例: "v1-g01"）。"""
    import yaml

    from .normalize import ROOT

    path = path or ROOT / "data" / "seed" / CHALLENGE_V0
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    stem = Path(path).stem                      # challenge_v0 / challenge_v1
    tag = stem.split("_")[-1]
    prefix = "x" if tag == "v0" else tag
    out = []
    for it in doc["items"]:
        pid = it["pid"]
        if pid not in catalog.products:          # 別カタログ（fixture 等）では該当商品だけ使う
            continue
        p = catalog.products[pid]
        ref = it.get("product_ref") or _challenge_ref(p, it.get("ref"))
        ctype = it["type"]
        attr = it.get("attribute") or {"compatibility": "compatible_with", "ingredient": "contains",
                                        "bundle": "includes"}.get(ctype)
        expected = it["expected"]
        out.append(Claim(
            claim_id=f"{prefix}-{it['id']}", claim_type=ctype, value=it["value"], attribute=attr, unit=it.get("unit"),
            polarity=it.get("polarity", "affirm"), product_ref=ref, product_id=pid,
            severity=severity_for(ctype, attr),
            provenance={"kind": "gold" if expected == "PASS" else "mutation", "generator": f"{stem} (handwritten)"},
            label={"expected": expected, "error_type": it.get("error_type"),
                   "rationale": {"method": "handwritten", "note": it.get("note", "")},
                   "evidence_in_snapshot": bool(it.get("evidence_in_snapshot", True))},
        ))
    return out
