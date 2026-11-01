#!/usr/bin/env python3
"""SpecGate — 回帰ゲート（ローカルと CI の両方で同じ検査）。

  1. オフライン pytest 全緑
  2. スプリットの漏れ検査（SpecBench v0 の loco / loro / temporal）
  3. 決定論 verifier による challenge v0+v1 の再採点リプレイを pinned baseline（configs/specgate_baseline.json）と比較
     fail 条件: BLOCK precision < 0.90、S1 素通し > 0、UNKNOWN 率が baseline から ±5pt を超えて変動
     challenge v1（未見 holdout）は公開ツリーに含めないため、ファイルが無い環境ではスキップとして記録する（fail にしない）

使い方:  python scripts/specgate.py [--skip-pytest] [--pin]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASELINE = ROOT / "configs" / "specgate_baseline.json"
REPORT = ROOT / "results" / "specgate.md"
MIN_BLOCK_PRECISION = 0.90
MAX_S1_LEAK = 0
UNKNOWN_DRIFT = 0.05


def run_pytest() -> dict:
    t0 = time.perf_counter()
    p = subprocess.run([sys.executable, "-m", "pytest", "-o", "addopts=", "-q", "-p", "no:warnings"], cwd=ROOT,
                       capture_output=True, text=True)
    tail = (p.stdout.strip().splitlines() or [""])[-1]
    return {"ok": p.returncode == 0, "summary": tail, "seconds": round(time.perf_counter() - t0, 1)}


def run_leak_check() -> dict:
    from src.claims import challenge_claims, gold_claims
    from src.mutate import mutations
    from src.snapshot import Catalog
    from src.splits import all_splits, leak_check

    cat = Catalog.from_seed()
    claims = gold_claims(cat) + mutations(cat) + challenge_claims(cat)
    issues = {s["split"]: leak_check(s, cat, claims) for s in all_splits(cat)}
    return {"ok": not any(issues.values()), "issues": {k: len(v) for k, v in issues.items()},
            "examples": [i for v in issues.values() for i in v][:5]}


def replay() -> dict:
    from src.claims import CHALLENGE_V0, CHALLENGE_V1, challenge_catalog, challenge_claims
    from src.evaluate import gate_row, run_system
    from src.verify import Verifier

    out = {}
    for tag, name in (("v0", CHALLENGE_V0), ("v1", CHALLENGE_V1)):
        path = ROOT / "data" / "seed" / name
        if not path.exists():
            out[tag] = {"skipped": "holdout not present in this tree"}
            continue
        cat = challenge_catalog(path)
        claims = challenge_claims(cat, path)
        res, _ = run_system(Verifier(cat), claims)
        r = gate_row(claims, res)
        out[tag] = {"n": len(claims), "block_precision": r["block_precision"], "s1_leak": len(r["s1_leak"]),
                    "s1_leak_ids": r["s1_leak"], "unknown_rate": r["unknown_rate"], "s2_recall": r["s2_recall"],
                    "gold_pass_rate": r["gold_pass_rate"]}
    return out


def judge(rep: dict, baseline: dict) -> list[str]:
    fails = []
    for tag, r in rep.items():
        if "skipped" in r:
            continue
        bp = r["block_precision"]
        if bp is None or bp < MIN_BLOCK_PRECISION:
            fails.append(f"{tag}: BLOCK precision {bp} < {MIN_BLOCK_PRECISION}")
        if r["s1_leak"] > MAX_S1_LEAK:
            fails.append(f"{tag}: S1 leak {r['s1_leak']} > {MAX_S1_LEAK} ({', '.join(r['s1_leak_ids'])})")
        base = (baseline.get("replay") or {}).get(tag)
        if base is None:
            fails.append(f"{tag}: no pinned baseline")
        elif abs(r["unknown_rate"] - base["unknown_rate"]) > UNKNOWN_DRIFT + 1e-9:
            fails.append(f"{tag}: UNKNOWN rate {r['unknown_rate']} drifted from baseline {base['unknown_rate']} "
                         f"by more than {UNKNOWN_DRIFT}")
    return fails


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-pytest", action="store_true")
    ap.add_argument("--pin", action="store_true", help="現在のリプレイ結果を baseline として固定する（意図的な変更時のみ）")
    a = ap.parse_args()
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    tests = {"ok": True, "summary": "skipped", "seconds": 0} if a.skip_pytest else run_pytest()
    leaks = run_leak_check()
    rep = replay()
    if a.pin:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        BASELINE.write_text(json.dumps({"pinned_at": started, "pinned_from_commit": commit or None,
                                        "replay": {k: v for k, v in rep.items() if "skipped" not in v}},
                                       ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"pinned baseline → {BASELINE.relative_to(ROOT)}")
    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
    fails = []
    if not tests["ok"]:
        fails.append(f"pytest failed: {tests['summary']}")
    if not leaks["ok"]:
        fails.append(f"leak check failed: {leaks['issues']}")
    fails += judge(rep, baseline)
    status = "PASS" if not fails else "FAIL"
    L = [f"# SpecGate — {status}", "", f"- 実行: {started}（`python scripts/specgate.py`）",
         f"- baseline: `{BASELINE.relative_to(ROOT)}`（pinned {baseline.get('pinned_at')}, commit {baseline.get('pinned_from_commit')}）",
         f"- 条件: BLOCK precision ≥ {MIN_BLOCK_PRECISION}、S1 素通し ≤ {MAX_S1_LEAK}、UNKNOWN 率の変動 ≤ ±{UNKNOWN_DRIFT * 100:.0f}pt", "",
         "| 検査 | 結果 | 詳細 |", "|---|---|---|",
         f"| pytest（オフライン） | {'OK' if tests['ok'] else 'NG'} | {tests['summary']}（{tests['seconds']} 秒） |",
         f"| スプリット漏れ検査 | {'OK' if leaks['ok'] else 'NG'} | {leaks['issues']} |"]
    for tag, r in rep.items():
        if "skipped" in r:
            L.append(f"| リプレイ challenge {tag} | SKIP | {r['skipped']} |")
            continue
        base = (baseline.get("replay") or {}).get(tag, {})
        L.append(f"| リプレイ challenge {tag} | {'OK' if not [f for f in fails if f.startswith(tag)] else 'NG'} | "
                 f"n={r['n']}、BLOCK precision {r['block_precision']}、S1 素通し {r['s1_leak']}、UNKNOWN {r['unknown_rate']}"
                 f"（baseline {base.get('unknown_rate')}）、S2 recall {r['s2_recall']}、gold PASS {r['gold_pass_rate']} |")
    if fails:
        L += ["", "## 失敗理由", ""] + [f"- {f}" for f in fails]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
