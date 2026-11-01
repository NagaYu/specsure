"""SpecBench のスプリット定義と漏れ検査。

- leave-one-category-out（カテゴリごとに 1 fold）
- leave-one-retailer-out（小売ごとに 1 fold）
- 時間スプリット（スナップショット取得日が cutoff 以降を test）

分割単位は商品。兄弟バリアント・地域版・世代違いが train/test を跨ぐのは「意図的な hard negative」であり、
跨るペアはすべて hard_negatives に明示する。漏れ検査は「明示されていない跨り」と「同一商品の跨り」を検出する。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from .identity import normalize_gtin

DEFAULT_TEMPORAL_CUTOFF = "2026-09-15"


def _fold(catalog, fold_id: str, held_out: str, test_ids: set[str]) -> dict:
    train = sorted(set(catalog.products) - test_ids)
    test = sorted(test_ids)
    hard = []
    for t in test:
        for r in train:
            kind = catalog.related_kind(t, r)
            if kind:
                hard.append({"test": t, "train": r, "kind": kind})
    return {"fold_id": fold_id, "held_out": held_out, "train": train, "test": test, "hard_negatives": hard}


def _group_split(catalog, name: str, key: Callable[[dict], str]) -> dict:
    groups: dict[str, set[str]] = {}
    for pid, p in catalog.products.items():
        groups.setdefault(key(p), set()).add(pid)
    folds = [_fold(catalog, f"{name}:{g}", g, ids) for g, ids in sorted(groups.items())]
    return {"split": name, "unit": "product", "folds": folds}


def leave_one_category_out(catalog) -> dict:
    return _group_split(catalog, "loco", lambda p: p["category"])


def leave_one_retailer_out(catalog) -> dict:
    return _group_split(catalog, "loro", lambda p: p["retailer"])


def temporal_split(catalog, cutoff: str = DEFAULT_TEMPORAL_CUTOFF) -> dict:
    c = datetime.fromisoformat(cutoff).date()
    test = {pid for pid, p in catalog.products.items() if datetime.fromisoformat(p["captured_at"]).date() >= c}
    fold = _fold(catalog, f"temporal:{cutoff}", f">= {cutoff}", test)
    return {"split": "temporal", "unit": "product", "cutoff": cutoff, "folds": [fold]}


def all_splits(catalog, cutoff: str = DEFAULT_TEMPORAL_CUTOFF) -> list[dict]:
    return [leave_one_category_out(catalog), leave_one_retailer_out(catalog), temporal_split(catalog, cutoff)]


def leak_check(split: dict, catalog, claims: Iterable = ()) -> list[str]:
    """問題の一覧を返す（空なら合格）。"""
    issues: list[str] = []
    gtins: dict[str, str] = {}
    for pid, p in catalog.products.items():
        g = normalize_gtin(p.get("gtin"))
        if g is None:
            continue
        if g in gtins:
            issues.append(f"GTIN {g} shared by {gtins[g]} and {pid}")
        gtins[g] = pid
    claims = list(claims)
    for fold in split["folds"]:
        fid = fold["fold_id"]
        train, test = set(fold["train"]), set(fold["test"])
        both = train & test
        if both:
            issues.append(f"{fid}: product_id in both train and test: {sorted(both)}")
        missing = set(catalog.products) - train - test
        if missing:
            issues.append(f"{fid}: products not assigned: {sorted(missing)}")
        flagged = {(h["test"], h["train"]) for h in fold["hard_negatives"]}
        for h in fold["hard_negatives"]:
            if h["test"] not in test or h["train"] not in train:
                issues.append(f"{fid}: hard negative {h} not across the split")
            elif catalog.related_kind(h["test"], h["train"]) != h["kind"]:
                issues.append(f"{fid}: hard negative {h} has wrong kind")
        for t in test:
            for r in train:
                if catalog.related_kind(t, r) and (t, r) not in flagged:
                    issues.append(f"{fid}: related pair {t} / {r} crosses the split without hard_negative flag")
        side = {**{p: "train" for p in train}, **{p: "test" for p in test}}
        for c in claims:
            if c.product_id is None:
                continue
            if c.product_id not in side:
                issues.append(f"{fid}: claim {c.claim_id} refers to unknown product {c.product_id}")
                continue
            # 関連商品（兄弟・地域版・世代）から値を借りた mutation が跨る場合は hard negative の明示が必要。
            # 無関係な商品から借りた値（成分の追加などのディストラクタ）は漏れではない
            src = ((c.label or {}).get("rationale") or {}).get("source_product_id")
            if (src and src in side and side[src] != side[c.product_id]
                    and catalog.related_kind(c.product_id, src)):
                pair = (c.product_id, src) if side[c.product_id] == "test" else (src, c.product_id)
                if pair not in flagged:
                    issues.append(f"{fid}: claim {c.claim_id} borrows a value from {src} across the split unflagged")
    return issues


def write_splits(splits: list[dict], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for s in splits:
        p = out_dir / f"{s['split']}.json"
        p.write_text(json.dumps(s, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        paths.append(p)
    return paths
