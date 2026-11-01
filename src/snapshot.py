"""メーカー仕様スナップショットの取り込み・保存と、verifier が参照するカタログ。

seed（data/seed/*.yaml, fixtures/*.yaml）はファミリー単位で書かれ、商品ごとの差分で上書きする。
取り込み時に: ソース台帳のゲート、本文の非混入、GTIN/単位の妥当性を検査する。
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import yaml

from .identity import IdentityIndex, Relation, gtin_scheme, normalize_gtin, parse_model
from .normalize import ROOT, UnitError, units
from .sources import check_no_raw_text, require_ingestable

DEFAULT_SEED = ROOT / "data" / "seed" / "specbench_v0.yaml"
FIXTURE_SEED = ROOT / "fixtures" / "seed_mini.yaml"
REGIONS = {"JP", "US"}
MERGE_BY_KEY = ("attributes", "region_spec")          # キー単位でマージ
REPLACE_WHOLE = ("compatibility", "ingredients", "bundle")
PRODUCT_FIELDS = ("product_id", "gtin", "gtin14", "gtin_scheme", "model_number", "model_base", "product_name",
                  "brand", "category",
                  "family_id", "line_id", "generation", "variant_axes", "region", "retailer", "source_id",
                  "source_url", "captured_at", "name_en", "name_ja", "snapshot_id")


class SeedError(ValueError):
    pass


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _canon_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validate_value(path: str, v: Any) -> None:
    if not isinstance(v, dict) or not ({"value"} <= v.keys() or {"min", "max"} <= v.keys()):
        raise SeedError(f"{path}: expected {{value[,unit]}} or {{min,max,unit}}, got {v!r}")
    if "unit" in v:
        try:
            units().canon(v["unit"])
        except UnitError as e:
            raise SeedError(f"{path}: {e}") from e


def expand_seed(seed: dict) -> list[tuple[dict, dict]]:
    """seed dict → [(product_record, snapshot)]。"""
    url_base = seed.get("source_url_base", "").rstrip("/")
    out = []
    seen: set[str] = set()
    for fam in seed["families"]:
        source = require_ingestable(fam.get("source_id") or seed["source_id"])   # ファミリー単位で出典を上書きできる
        for prod in fam["products"]:
            pid = prod["product_id"]
            if pid in seen:
                raise SeedError(f"duplicate product_id {pid}")
            seen.add(pid)
            region = prod["region"]
            if region not in REGIONS:
                raise SeedError(f"{pid}: region must be one of {REGIONS}")
            raw_gtin = prod.get("gtin")
            g14 = normalize_gtin(raw_gtin) if raw_gtin else None
            if raw_gtin and g14 is None:
                raise SeedError(f"{pid}: invalid GTIN {raw_gtin!r}")
            if not (raw_gtin or prod.get("model_number") or prod.get("product_name")):
                raise SeedError(f"{pid}: needs at least one of gtin / model_number / product_name")
            captured = datetime.fromisoformat(prod["captured_at"])
            model = prod.get("model_number")
            name = fam.get("name") or {}
            record = {
                "product_id": pid,
                "gtin": str(raw_gtin) if raw_gtin else None,
                "gtin14": g14,
                "gtin_scheme": (None if not raw_gtin else
                                "JAN" if region == "JP" and gtin_scheme(raw_gtin) == "EAN-13" else gtin_scheme(raw_gtin)),
                "product_name": prod.get("product_name"),
                "model_number": model,
                "model_base": parse_model(model).base if model else None,
                "brand": fam["brand"],
                "category": fam["category"],
                "family_id": fam["family_id"],
                "line_id": fam.get("line_id", fam["family_id"]),
                "generation": int(fam.get("generation", 1)),
                "variant_axes": dict(prod.get("variant") or {}),
                "region": region,
                "retailer": prod["retailer"],
                "source_id": source.source_id,
                "source_url": prod.get("source_url") or f"{url_base}/{fam['family_id']}/{pid}",
                "captured_at": captured.isoformat(),
                "name_en": name.get("en"),
                "name_ja": name.get("ja"),
            }
            body: dict[str, Any] = {}
            for sec in MERGE_BY_KEY:
                merged = _merge(fam.get(sec) or {}, prod.get(sec) or {})
                if merged:
                    body[sec] = merged
            for sec in REPLACE_WHOLE:
                val = prod[sec] if sec in prod else fam.get(sec)
                if val:
                    body[sec] = copy.deepcopy(val)
            for sec in MERGE_BY_KEY:
                for k, v in (body.get(sec) or {}).items():
                    _validate_value(f"{pid}.{sec}.{k}", v)
            body["variant_axes"] = record["variant_axes"]
            snap_id = f"snap-{pid}-{captured.date().isoformat()}"
            snapshot = {
                "snapshot_id": snap_id,
                "product_id": pid,
                "source_id": source.source_id,
                "source_url": record["source_url"],
                "captured_at": record["captured_at"],
                **body,
            }
            snapshot["content_hash"] = hashlib.sha256(_canon_json(body).encode()).hexdigest()[:16]
            check_no_raw_text(snapshot)
            record["snapshot_id"] = snap_id
            out.append((record, snapshot))
    return out


def load_seed(path: Path | str = DEFAULT_SEED) -> list[tuple[dict, dict]]:
    with open(path, encoding="utf-8") as f:
        return expand_seed(yaml.safe_load(f))


@dataclass
class Catalog:
    products: dict[str, dict]
    snapshots: dict[str, dict]                      # product_id → snapshot
    identity: IdentityIndex = field(init=False)

    def __post_init__(self):
        self.identity = IdentityIndex(self.products)

    @classmethod
    def from_pairs(cls, pairs: Iterable[tuple[dict, dict]]) -> "Catalog":
        pairs = list(pairs)
        return cls({p["product_id"]: p for p, _ in pairs}, {p["product_id"]: s for p, s in pairs})

    @classmethod
    def from_seed(cls, path: Path | str = DEFAULT_SEED) -> "Catalog":
        return cls.from_pairs(load_seed(path))

    @classmethod
    def from_jsonl(cls, products_path: Path, snapshots_path: Path) -> "Catalog":
        prods = [json.loads(l) for l in products_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        snaps = [json.loads(l) for l in snapshots_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        return cls({p["product_id"]: p for p in prods}, {s["product_id"]: s for s in snaps})

    # 関係
    def family(self, pid: str) -> list[str]:
        fid = self.products[pid]["family_id"]
        return sorted(q for q, p in self.products.items() if p["family_id"] == fid and q != pid)

    def variant_siblings(self, pid: str) -> list[str]:
        """同一ファミリー・同一地域の兄弟バリアント"""
        r = self.products[pid]["region"]
        return [q for q in self.family(pid) if self.products[q]["region"] == r]

    def region_siblings(self, pid: str) -> list[str]:
        r = self.products[pid]["region"]
        return [q for q in self.family(pid) if self.products[q]["region"] != r]

    def generation_neighbors(self, pid: str) -> list[str]:
        p = self.products[pid]
        return sorted(q for q, o in self.products.items()
                      if o["line_id"] == p["line_id"] and o["family_id"] != p["family_id"])

    def related_kind(self, a: str, b: str) -> str | None:
        """hard negative の種別。無関係なら None"""
        if a == b:
            return None
        if self.identity.relation(a, b) is Relation.SIBLING_VARIANT:
            return "sibling_variant" if self.products[a]["region"] == self.products[b]["region"] else "region_variant"
        if self.products[a]["line_id"] == self.products[b]["line_id"]:
            return "generation"
        return None


def write_catalog(catalog: Catalog, manifest_dir: Path, snapshot_dir: Path) -> dict[str, Path]:
    import pandas as pd

    manifest_dir.mkdir(parents=True, exist_ok=True)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    rows = [{k: catalog.products[pid].get(k) for k in PRODUCT_FIELDS} for pid in sorted(catalog.products)]
    jsonl = manifest_dir / "products.jsonl"
    jsonl.write_text("".join(_canon_json(r) + "\n" for r in rows), encoding="utf-8")
    df = pd.DataFrame([{**r, "variant_axes": _canon_json(r["variant_axes"])} for r in rows])
    parquet = manifest_dir / "products.parquet"
    df.to_parquet(parquet, index=False)
    snaps = snapshot_dir / "snapshots.jsonl"
    snaps.write_text("".join(_canon_json(catalog.snapshots[pid]) + "\n" for pid in sorted(catalog.snapshots)),
                     encoding="utf-8")
    return {"products_jsonl": jsonl, "products_parquet": parquet, "snapshots": snaps}
