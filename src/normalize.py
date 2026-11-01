"""文字列・単位・語彙の正規化。

- 文字列キー: NFKC（全角→半角）→ casefold → 空白・ハイフン類の除去
- 単位: configs/units.yaml の換算テーブルで SI 基準単位に統一（外部ライブラリは使わない）
- 許容誤差: configs/tolerances.yaml（カテゴリ別・属性別）
- 語彙: configs/vocab.yaml（色・汎用語・成分・成分クラス・ブランドの日英表記揺れ）
"""

from __future__ import annotations

import functools
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "configs"


@functools.lru_cache(maxsize=None)
def load_config(name: str) -> dict:
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ───────────────────────────── 文字列 ─────────────────────────────

_TEXT_STRIP = re.compile(r"[\s\-‐‑‒–—―−_・·.,'’]+")


def nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s)


def text_key(s: Any) -> str:
    """照合用キー。カタカナ長音「ー」は語の一部なので残す（型番用の正規化は identity.py）。"""
    if s is None:
        return ""
    return _TEXT_STRIP.sub("", nfkc(str(s)).casefold())


# ───────────────────────────── 単位 ─────────────────────────────

@dataclass(frozen=True)
class Quantity:
    value: float
    unit: str            # 正規単位名（units.yaml の units のキー）
    dimension: str

    def si(self) -> float:
        return self.value * units().factor(self.dimension, self.unit)


class UnitError(ValueError):
    pass


class Units:
    def __init__(self, cfg: dict):
        self.dimensions: dict[str, dict] = cfg["dimensions"]
        self.aliases = {self._raw_key(k): v for k, v in (cfg.get("aliases") or {}).items()}
        self.ambiguous = cfg.get("ambiguous") or {}
        self.confusable = cfg.get("confusable") or {}
        self.size_systems = cfg.get("size_systems") or {}
        self._unit_dims: dict[str, list[str]] = {}
        for dim, spec in self.dimensions.items():
            for u in spec["units"]:
                self._unit_dims.setdefault(str(u), []).append(dim)

    @staticmethod
    def _raw_key(u: str) -> str:
        return re.sub(r"\s+", " ", nfkc(str(u)).strip().casefold())

    def canon(self, unit: str | None, prefer_dimension: str | None = None) -> tuple[str, str]:
        """単位文字列 → (次元, 正規単位名)。未知の単位は UnitError。"""
        key = self._raw_key(unit or "")
        key = self.aliases.get(key, key)
        if key not in self._unit_dims and key.replace(" ", "") in self._unit_dims:
            key = key.replace(" ", "")
        if key in self.ambiguous and prefer_dimension in self.ambiguous[key]:
            key = self.ambiguous[key][prefer_dimension]
        dims = self._unit_dims.get(key)
        if not dims:
            raise UnitError(f"unknown unit: {unit!r}")
        if prefer_dimension in dims:
            return prefer_dimension, key
        return dims[0], key

    def factor(self, dimension: str, unit: str) -> float:
        return float(self.dimensions[dimension]["units"][unit])

    def si_unit(self, dimension: str) -> str:
        return str(self.dimensions[dimension]["si"])

    def quantity(self, value: float, unit: str | None, prefer_dimension: str | None = None) -> Quantity:
        dim, u = self.canon(unit, prefer_dimension)
        return Quantity(float(value), u, dim)

    def convert(self, q: Quantity, unit: str) -> float:
        dim, u = self.canon(unit, q.dimension)
        if dim != q.dimension:
            raise UnitError(f"cannot convert {q.dimension} to {dim}")
        return q.si() / self.factor(dim, u)

    def shoe_size_cm(self, system: str, label: str) -> float | None:
        """'US 9' のような表記を cm に換算（目安表）。'27.0cm' / '27' はそのまま cm として読む。"""
        s = nfkc(label).strip().upper().replace("CM", "").strip()
        m = re.match(r"^(US)\s*([\d.]+)$", s)
        table = self.size_systems.get(system, {})
        if m:
            key = m.group(2).rstrip("0").rstrip(".") if "." in m.group(2) else m.group(2)
            v = table.get(m.group(1), {}).get(key)
            return float(v) if v is not None else None
        try:
            return float(s.replace("JP", "").strip())
        except ValueError:
            return None


@functools.lru_cache(maxsize=None)
def units() -> Units:
    return Units(load_config("units.yaml"))


_QTY_RE = re.compile(r"^\s*([-+]?\d[\d,]*(?:\.\d+)?)\s*(.*?)\s*$")


def _strip_value_prefix(t: str) -> str:
    """「約」「approx.」「AC」などの前置き（units.yaml の value_prefixes）を落とす"""
    prefixes = sorted((nfkc(x).casefold() for x in load_config("units.yaml").get("value_prefixes") or []),
                      key=len, reverse=True)
    t = t.strip()
    changed = True
    while changed:
        changed = False
        for pre in prefixes:
            if t.casefold().startswith(pre):
                t = t[len(pre):].strip()
                changed = True
                break
    return t


def parse_quantity(text: Any) -> tuple[float, str] | None:
    """'65W' / '6.1 インチ' / '20,000 mAh' / '約8時間' / 'AC100V' → (値, 単位文字列)。数値で始まらなければ None。"""
    q = parse_quantity_text(text)
    return None if q is None else (float(q[0].replace(",", "")), q[1])


def parse_quantity_text(text: Any) -> tuple[str, str] | None:
    """parse_quantity と同じだが、数値部分を表記のまま返す（表記精度の判定用）"""
    if text is None:
        return None
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return _number_text(text), ""
    m = _QTY_RE.match(_strip_value_prefix(nfkc(str(text))))
    if not m:
        return None
    return m.group(1), m.group(2)


def _number_text(x: Any) -> str:
    return str(x) if isinstance(x, (int, str)) else repr(float(x))


def precision_floor_si(number: Any, unit: str | None, dimension: str) -> float:
    """表記精度フロア: 数値表記の最終桁の単位（その単位で）を SI 化した半分。'6.6 ft' → 0.05 ft。"""
    if dimension == "dimensionless":
        return 0.5 * decimal_step(number)
    d, u = units().canon(unit, dimension)
    return 0.5 * decimal_step(number) * units().factor(d, u)


def decimal_step(number: Any) -> float:
    """表記の最終桁の単位。'6.6' → 0.1、'400' → 1、6.0 → 0.1（表記どおり）"""
    from decimal import Decimal, InvalidOperation

    try:
        exp = Decimal(_number_text(number).replace(",", "")).as_tuple().exponent
    except InvalidOperation:
        return 0.0
    return float(Decimal(1).scaleb(exp)) if isinstance(exp, int) else 0.0


# ───────────────────────────── 許容誤差 ─────────────────────────────

class Tolerances:
    def __init__(self, cfg: dict):
        self.cfg = cfg

    def _abs_si(self, spec: dict, dimension: str) -> float:
        raw = spec.get("abs")
        if raw in (None, "", 0):
            return 0.0
        parsed = parse_quantity(raw)
        if parsed is None:
            raise UnitError(f"bad tolerance abs: {raw!r}")
        val, unit = parsed
        if dimension == "dimensionless":
            return val
        return units().quantity(val, unit, dimension).si()

    def spec_for(self, category: str | None, attribute: str | None, dimension: str) -> dict:
        """優先順位: カテゴリ（最長一致）の属性 > カテゴリの次元 > 属性 > 既定"""
        spec = dict(self.cfg["default"].get(dimension, {"abs": 0, "rel": 0.0}))
        attr_spec = (self.cfg.get("attributes") or {}).get(attribute or "")
        if attr_spec:
            spec.update(attr_spec)
        cats = self.cfg.get("categories") or {}
        if category:
            parts = category.split(".")
            for i in range(1, len(parts) + 1):     # 短い接頭辞から順に上書き → 最長一致が勝つ
                c = cats.get(".".join(parts[:i]))
                if not c:
                    continue
                if dimension in (c.get("dimensions") or {}):
                    spec.update(c["dimensions"][dimension])
                if (attribute or "") in (c.get("attributes") or {}):
                    spec.update(c["attributes"][attribute])
        return spec

    def tolerance_si(self, category: str | None, attribute: str | None, dimension: str, gold_si: float) -> float:
        spec = self.spec_for(category, attribute, dimension)
        return max(self._abs_si(spec, dimension), float(spec.get("rel", 0.0)) * abs(gold_si))

    def within(self, claim_si: float, gold_si: float, tol_si: float) -> bool:
        # 境界は含む。浮動小数の丸め誤差だけは吸収する
        return abs(claim_si - gold_si) <= tol_si + 1e-9 * max(1.0, abs(gold_si))


@functools.lru_cache(maxsize=None)
def tolerances() -> Tolerances:
    return Tolerances(load_config("tolerances.yaml"))


# ───────────────────────────── 語彙 ─────────────────────────────

@dataclass(frozen=True)
class IngredientRef:
    key: str                     # 正規の成分 ID / クラス ID / 未認識なら text_key
    is_class: bool
    classes: frozenset[str]
    recognized: bool


class Vocab:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.colors = self._index(cfg.get("colors") or {})
        self.colors.update(self._index(cfg.get("color_variants") or {}))
        self.terms = self._index(cfg.get("terms") or {})
        self.classes = self._index(cfg.get("ingredient_classes") or {})
        self.ingredients: dict[str, tuple[str, frozenset[str]]] = {}
        for cid, spec in (cfg.get("ingredients") or {}).items():
            classes = frozenset(spec.get("classes") or [])
            for n in [cid, *spec.get("names", [])]:
                self.ingredients[text_key(n)] = (cid, classes)
        self.brand_aliases: dict[str, str] = {}
        for brand, aliases in (cfg.get("brands") or {}).items():
            for a in [brand, *aliases]:
                self.brand_aliases[nfkc(a).casefold()] = brand.casefold()
        self.compat_phrases = {nfkc(k).casefold(): nfkc(v).casefold()
                               for k, v in (cfg.get("compat_phrases") or {}).items()}
        self.mandatory_allergens = {k: frozenset(v) for k, v in (cfg.get("mandatory_allergens") or {}).items()}
        self.compat_noise = [nfkc(w).casefold() for w in (cfg.get("compat_noise_words") or [])]

    @staticmethod
    def _index(table: dict) -> dict[str, str]:
        out = {}
        for canon, names in table.items():
            for n in [canon, *names]:
                out[text_key(n)] = canon
        return out

    def color(self, s: Any) -> str | None:
        return self.colors.get(text_key(s))

    def term(self, s: Any) -> str:
        """色 → 汎用語 → 生キーの順で正規化"""
        k = text_key(s)
        return self.colors.get(k) or self.terms.get(k) or k

    def ingredient(self, s: Any) -> IngredientRef:
        k = text_key(s)
        if k in self.ingredients:
            cid, classes = self.ingredients[k]
            return IngredientRef(cid, False, classes, True)
        if k in self.classes:
            c = self.classes[k]
            return IngredientRef(c, True, frozenset([c]), True)
        return IngredientRef(k, False, frozenset(), False)

    def compat_key(self, s: Any, brandless: bool = False) -> str:
        """互換性項目の照合キー: ブランドの日英表記を統一し、句の揺れを置換してから区切りを除去。
        brandless=True ではブランド名と機器種別の一般語（printer 等、vocab の compat_noise_words）も落とす。"""
        t = nfkc(str(s)).casefold()
        for alias, brand in sorted(self.brand_aliases.items(), key=lambda kv: -len(kv[0])):
            t = t.replace(alias, "" if brandless else brand)
        for phrase, repl in sorted(self.compat_phrases.items(), key=lambda kv: -len(kv[0])):
            t = re.sub(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", repl, t)
        if brandless:
            for w in sorted(self.compat_noise, key=len, reverse=True):
                t = re.sub(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", "", t)
        return re.sub(r"[\s\-‐‑‒–—―−_・·.,'’()（）]+", "", t)

    def has_brand(self, s: Any) -> bool:
        t = nfkc(str(s)).casefold()
        return any(a in t for a in self.brand_aliases)


@functools.lru_cache(maxsize=None)
def vocab() -> Vocab:
    return Vocab(load_config("vocab.yaml"))


def is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and not math.isnan(float(x))
