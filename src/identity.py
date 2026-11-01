"""商品同一性: GTIN/JAN を第一キー、型番を第二キーとして解決する。

型番の正規化（configs/identity.yaml）:
  NFKC（全角→半角）→ 大文字化 → 区切り（ハイフン揺れ・長音・括弧・空白）で分割 →
  末尾トークンから 地域 / 色 / 枝番 / 容量 のサフィックスを分離 → 残りを連結した base キー

relation() は「同一商品 / 兄弟バリアント / 別商品」を判定する。
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

from .normalize import load_config, nfkc, text_key, vocab


class Relation(str, Enum):
    SAME = "same"
    SIBLING_VARIANT = "sibling_variant"
    DIFFERENT = "different"


# ───────────────────────────── GTIN ─────────────────────────────

def gtin_check_digit(body: str) -> str:
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return str((10 - total % 10) % 10)


def normalize_gtin(raw: Any) -> str | None:
    """GTIN-8 / UPC-A / EAN-13(JAN) / GTIN-14 を GTIN-14 に正規化。桁数かチェックデジットが不正なら None。"""
    if raw is None:
        return None
    digits = re.sub(r"[\s\-]", "", nfkc(str(raw)))
    if not digits.isdigit():
        return None
    if len(digits) not in load_config("identity.yaml")["gtin_lengths"]:
        return None
    if gtin_check_digit(digits[:-1]) != digits[-1]:
        return None
    return digits.zfill(14)


def gtin_scheme(raw: str) -> str:
    d = re.sub(r"\D", "", nfkc(raw))
    return {8: "GTIN-8", 12: "UPC-A", 13: "EAN-13", 14: "GTIN-14"}.get(len(d), "unknown")


# ───────────────────────────── 型番 ─────────────────────────────

@dataclass(frozen=True)
class ModelKey:
    raw: str
    base: str                                    # サフィックスを除いた比較キー（区切りなし）
    tokens: tuple[str, ...]
    suffix: tuple[tuple[str, str], ...] = ()     # (kind, value) 例: (("color","black"),("region","US"))

    @property
    def suffix_map(self) -> dict[str, str]:
        return dict(self.suffix)

    @property
    def full(self) -> str:
        return self.base + "".join(f"|{k}={v}" for k, v in sorted(self.suffix))


@functools.lru_cache(maxsize=None)
def _rules() -> dict:
    cfg = load_config("identity.yaml")
    seps = sorted(cfg["separators"], key=len, reverse=True)
    return {
        "split": re.compile("|".join(re.escape(s) for s in seps)),
        "rules": cfg["suffix_rules"],
        "branch": re.compile(cfg["suffix_rules"]["branch"]["pattern"]),
        "capacity": re.compile(cfg["suffix_rules"]["capacity"]["pattern"]),
    }


def _classify_suffix(kind: str, token: str) -> str | None:
    r = _rules()
    rules = r["rules"]
    if kind == "region":
        return token if token in rules["region"]["codes"] else None
    if kind == "color":
        code = rules["color"]["codes"].get(token)
        if code is None and len(token) >= 3:          # 「KP-20K (BLACK)」のような色名（vocab の色語彙）
            code = vocab().color(token)
        return code
    if kind == "branch":
        return token.zfill(2) if r["branch"].match(token) else None
    if kind == "capacity":
        return token if r["capacity"].match(token) else None
    raise ValueError(kind)


def parse_model(raw: str, brands: Iterable[str] = ()) -> ModelKey:
    s = nfkc(str(raw)).upper().strip()
    tokens = [t for t in _rules()["split"].split(s) if t]
    brand_set = {nfkc(b).upper() for b in brands}
    brand_set |= {a.upper() for a in vocab().brand_aliases}
    if len(tokens) > 1 and tokens[0] in brand_set:      # 「Kestrelon KC-PD65」の先頭ブランドを落とす
        tokens = tokens[1:]
    rules = _rules()["rules"]
    suffix: list[tuple[str, str]] = []
    remaining = list(tokens)
    pending = list(rules["order"])
    changed = True
    while changed:                       # 末尾から、各種別を一度ずつ（順不同: KC-PD65-BK-US も YK-HD12-US-W も可）
        changed = False
        for kind in pending:
            if len(remaining) <= rules[kind]["min_head"]:
                continue
            v = _classify_suffix(kind, remaining[-1])
            if v is not None:
                suffix.append((kind, v))
                remaining.pop()
                pending.remove(kind)
                changed = True
                break
    return ModelKey(raw=str(raw), base="".join(remaining), tokens=tuple(tokens), suffix=tuple(sorted(suffix)))


def compare_models(a: str, b: str) -> Relation:
    """文字列だけによる比較。base が同じでサフィックスが矛盾しなければ SAME、矛盾すれば兄弟。"""
    ka, kb = parse_model(a), parse_model(b)
    if ka.base != kb.base:
        return Relation.DIFFERENT
    ma, mb = ka.suffix_map, kb.suffix_map
    for k in set(ma) & set(mb):
        if ma[k] != mb[k]:
            return Relation.SIBLING_VARIANT
    # 片方にだけ色・容量・地域がある場合は、部分指定とみなして同一
    return Relation.SAME


# ───────────────────────────── カタログ照合 ─────────────────────────────

class ResolveStatus(str, Enum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"          # 兄弟バリアントのどれか特定できない
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"            # GTIN と型番が別の商品を指す（GTIN を採用して記録）


@dataclass
class Resolution:
    status: ResolveStatus
    product_id: str | None = None
    candidates: list[str] = field(default_factory=list)
    via: str | None = None           # "gtin" / "model"
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.product_id is not None


class IdentityIndex:
    """products: product_id → {gtin, model_number, brand, family_id, region, ...}"""

    def __init__(self, products: Mapping[str, Mapping[str, Any]]):
        self.products = products
        self.by_gtin: dict[str, str] = {}
        self.by_base: dict[str, list[str]] = {}
        self.by_name: dict[str, list[str]] = {}       # 第三キー: 商品名（JAN・型番がページに無い食品など、完全一致のみ）
        self.keys: dict[str, ModelKey] = {}
        brands = {p.get("brand") for p in products.values() if p.get("brand")}
        self.brands = sorted(brands)
        for pid, p in sorted(products.items()):
            g = normalize_gtin(p.get("gtin"))
            if g:
                if g in self.by_gtin and self.by_gtin[g] != pid:
                    raise ValueError(f"duplicate GTIN {g}: {self.by_gtin[g]} / {pid}")
                self.by_gtin[g] = pid
            if p.get("model_number"):
                k = parse_model(p["model_number"], self.brands)
                self.keys[pid] = k
                self.by_base.setdefault(k.base, []).append(pid)
            if p.get("product_name"):
                self.by_name.setdefault(text_key(p["product_name"]), []).append(pid)

    def resolve_model(self, model: str) -> Resolution:
        k = parse_model(model, self.brands)
        cands = self.by_base.get(k.base, [])
        if not cands:
            return Resolution(ResolveStatus.NOT_FOUND, notes=[f"model base {k.base!r} not in catalog"])
        exact = [pid for pid in cands if self.keys[pid].suffix_map == k.suffix_map]
        if len(exact) == 1:
            return Resolution(ResolveStatus.RESOLVED, exact[0], cands, "model")
        given = k.suffix_map
        compatible = [pid for pid in cands
                      if all(self.keys[pid].suffix_map.get(kind) == v for kind, v in given.items())]
        if len(compatible) == 1:
            return Resolution(ResolveStatus.RESOLVED, compatible[0], cands, "model")
        if not compatible:
            return Resolution(ResolveStatus.NOT_FOUND, candidates=cands,
                              notes=[f"variant suffix {given} not in family of {k.base!r}"])
        return Resolution(ResolveStatus.AMBIGUOUS, candidates=compatible,
                          notes=[f"{len(compatible)} sibling variants match {model!r}"])

    def resolve_name(self, name: str) -> Resolution:
        cands = self.by_name.get(text_key(name), [])
        if len(cands) == 1:
            return Resolution(ResolveStatus.RESOLVED, cands[0], cands, "name")
        if cands:
            return Resolution(ResolveStatus.AMBIGUOUS, candidates=cands, notes=[f"product name {name!r} is not unique"])
        return Resolution(ResolveStatus.NOT_FOUND, notes=[f"product name {name!r} not in catalog (exact match only)"])

    def resolve(self, gtin: Any = None, model: Any = None, name: Any = None) -> Resolution:
        """GTIN（第一キー）→ 型番（第二キー）→ 商品名の完全一致（第三キー）"""
        if gtin in (None, "") and not model and name:
            return self.resolve_name(name)
        notes: list[str] = []
        by_model = self.resolve_model(model) if model else None
        if gtin not in (None, ""):
            g = normalize_gtin(gtin)
            if g is None:
                notes.append(f"invalid GTIN {gtin!r} (length or check digit)")
            elif g in self.by_gtin:
                pid = self.by_gtin[g]
                if by_model and by_model.ok and by_model.product_id != pid:
                    return Resolution(ResolveStatus.CONFLICT, pid, [pid, by_model.product_id], "gtin",
                                      notes + [f"model {model!r} points to {by_model.product_id}; GTIN wins"])
                return Resolution(ResolveStatus.RESOLVED, pid, [pid], "gtin", notes)
            else:
                notes.append(f"GTIN {g} not in catalog")
        if by_model is not None:
            by_model.notes = notes + by_model.notes
            if not by_model.ok and name:
                by_name = self.resolve_name(name)
                if by_name.ok:
                    by_name.notes = by_model.notes + by_name.notes
                    return by_name
            return by_model
        if name:
            r = self.resolve_name(name)
            r.notes = notes + r.notes
            return r
        return Resolution(ResolveStatus.NOT_FOUND, notes=notes or ["no identifier"])

    def relation(self, a: str, b: str) -> Relation:
        """カタログ上の 2 商品の関係。GTIN 一致 = 同一、family 一致 = 兄弟バリアント、それ以外 = 別商品。"""
        if a == b:
            return Relation.SAME
        pa, pb = self.products[a], self.products[b]
        ga, gb = normalize_gtin(pa.get("gtin")), normalize_gtin(pb.get("gtin"))
        if ga and ga == gb:
            return Relation.SAME
        if pa.get("family_id") and pa.get("family_id") == pb.get("family_id"):
            return Relation.SIBLING_VARIANT
        return Relation.DIFFERENT
