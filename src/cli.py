"""SpecSure CLI。

  python -m src.cli sources            docs/SOURCES.md を configs/sources.yaml から再生成
  python -m src.cli build              seed → スナップショット・マニフェスト・claim・スプリット（漏れ検査つき）
  python -m src.cli evaluate           評価（決定論 v1 / naive）→ results/eval.{md,json}, results/GATE.md
  python -m src.cli evaluate --llm-judge   実キーで LLM judge も回す（SPECSURE_ALLOW_NETWORK=1 が必要）
  python -m src.cli all                build + evaluate
  python -m src.cli gate-v1            challenge v0（開発）+ v1（未見）で 3 方式比較 → results/gate-v1.{md,json}
                                       v1 は 1 回だけ実行する前提（結果があれば --force なしでは再実行しない）
  python -m src.cli gate-v1 --estimate-only   LLM judge の呼び出し回数と概算コストだけ表示
  SPECSURE_ALLOW_NETWORK=1 python -m src.cli gate-judge   LLM judge 比較（v0+v1 の 180 件）→ results/gate-judge.{md,json}
                                       見積もりを表示してから実行。gate-v1.* は書き換えない。--estimate-only で見積もりのみ
  python -m src.cli extract-eval       自由文→claim 抽出の 3 セット評価（v2 holdout は 1 回だけ実行）→ results/extract-v2-holdout.md
  python -m src.cli validate-records <file.jsonl>   収集記録（docs/collection-protocol.md の形式）を検証
  python -m src.cli claimwatch-sample  合成回答による ClaimWatch 証跡パックの見本 → results/claimwatch/
  python -m src.cli real-pilot         実データ 30 商品（data/real_pilot/）の取り込み・評価・取り込み時間 → results/real_pilot.md
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from .claims import challenge_claims, gold_claims, probe_claims, write_jsonl
from .mutate import mutations
from .normalize import ROOT
from .snapshot import DEFAULT_SEED, Catalog, write_catalog
from .sources import write_sources_md
from .splits import DEFAULT_TEMPORAL_CUTOFF, all_splits, leak_check, write_splits

DATA = ROOT / "data"


def cmd_build(seed_path: Path, seed: int, cutoff: str) -> int:
    cat = Catalog.from_seed(seed_path)
    paths = write_catalog(cat, DATA / "manifests", DATA / "snapshots")
    gold, muts, probes, chal = gold_claims(cat), mutations(cat, seed=seed), probe_claims(cat), challenge_claims(cat)
    write_jsonl(gold, DATA / "claims" / "gold.jsonl")
    write_jsonl(muts, DATA / "claims" / "mutations.jsonl")
    write_jsonl(probes, DATA / "claims" / "probes.jsonl")
    write_jsonl(chal, DATA / "claims" / "challenge.jsonl")
    splits = all_splits(cat, cutoff)
    issues = {s["split"]: leak_check(s, cat, gold + muts + chal) for s in splits}
    write_splits(splits, DATA / "splits")
    prods = cat.products.values()
    summary = {
        "seed_file": str(seed_path.relative_to(ROOT)),
        "mutation_seed": seed,
        "products": len(cat.products),
        "by_region": dict(Counter(p["region"] for p in prods)),
        "by_top_category": dict(Counter(p["category"].split(".")[0] for p in prods)),
        "by_category": dict(sorted(Counter(p["category"] for p in prods).items())),
        "by_retailer": dict(sorted(Counter(p["retailer"] for p in prods).items())),
        "families": len({p["family_id"] for p in prods}),
        "claims": {"gold": len(gold), "mutation": len(muts), "probe": len(probes), "challenge": len(chal)},
        "gold_by_type": dict(Counter(c.claim_type for c in gold)),
        "mutation_by_error_type": dict(sorted(Counter(c.label["error_type"] for c in muts).items())),
        "mutation_by_severity": dict(sorted(Counter(c.severity for c in muts).items())),
        "splits": {s["split"]: {"folds": len(s["folds"]),
                                "hard_negative_pairs": sum(len(f["hard_negatives"]) for f in s["folds"]),
                                "leak_issues": len(issues[s["split"]])} for s in splits},
        "temporal_cutoff": cutoff,
    }
    (DATA / "manifests" / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n",
                                                     encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    bad = {k: v for k, v in issues.items() if v}
    if bad:
        print("LEAK CHECK FAILED:", json.dumps(bad, ensure_ascii=False, indent=1), file=sys.stderr)
        return 1
    print(f"leak check: OK ({', '.join(issues)}) / wrote {', '.join(str(p.relative_to(ROOT)) for p in paths.values())}")
    return 0


def cmd_gate_v1(llm: bool, force: bool, estimate_only: bool) -> int:
    from .baselines import DEFAULT_LLM_MODEL
    from .evaluate import GATE_V1_JSON, challenge_sets, default_systems, llm_call_estimate, run_gate_v1

    import os
    est = llm_call_estimate(challenge_sets(), os.environ.get("SPECSURE_LLM_MODEL", DEFAULT_LLM_MODEL))
    print(f"LLM judge (challenge v0+v1): {est['claims']} claims → {est['calls']} calls, "
          f"~{est['est_input_tokens']:,} input + ~{est['est_output_tokens']:,} output tokens, "
          f"~${est['est_cost_usd']} on {est['model']} (概算)")
    if estimate_only:
        return 0
    if GATE_V1_JSON.exists() and not force:
        print(f"{GATE_V1_JSON.relative_to(ROOT)} already exists: challenge v1 is run once. Use --force to overwrite.",
              file=sys.stderr)
        return 2
    judge, status = None, f"未実施（2 回目）。実キーなし。challenge v0+v1 の 180 件で必要な呼び出しは {est['calls']} 回（概算 ${est['est_cost_usd']}）"
    if llm:
        from .baselines import AnthropicLLMJudge
        judge = AnthropicLLMJudge()
        status = f"実施（{judge.model}, effort={judge.effort}, {est['calls']} 回呼び出し）"
    rep = run_gate_v1(lambda cat: default_systems(cat, judge), status)
    for name, by in rep["systems"].items():
        for tag, r in by.items():
            print(f"{name:18s} {tag} BLOCK-P={r['block_precision']} S1-leak={len(r['s1_leak'])} "
                  f"S2-R={r['s2_recall']} goldPASS={r['gold_pass_rate']}")
    print("wrote results/gate-v1.md, results/gate-v1.json")
    return 0


def cmd_gate_judge(force: bool, estimate_only: bool, max_calls: int) -> int:
    """LLM judge 比較（challenge v0+v1 の 180 件）。results/gate-judge.{md,json} のみ書く。モックは使わない。"""
    import os

    from .baselines import DEFAULT_LLM_MODEL, NETWORK_ENV
    from .evaluate import GATE_JUDGE_JSON, challenge_sets, default_systems, llm_call_estimate, run_gate_judge

    sets = challenge_sets()
    est = llm_call_estimate(sets, os.environ.get("SPECSURE_LLM_MODEL", DEFAULT_LLM_MODEL))
    print(f"LLM judge (challenge v0+v1): {est['claims']} claims → {est['calls']} calls, "
          f"~{est['est_input_tokens']:,} input + ~{est['est_output_tokens']:,} output tokens, "
          f"~${est['est_cost_usd']} on {est['model']} (概算)")
    if estimate_only:
        return 0
    if est["calls"] > max_calls:
        print(f"refusing: {est['calls']} calls exceeds --max-calls {max_calls}", file=sys.stderr)
        return 2
    if GATE_JUDGE_JSON.exists() and not force:
        print(f"{GATE_JUDGE_JSON.relative_to(ROOT)} already exists (re-running costs money). Use --force to overwrite.",
              file=sys.stderr)
        return 2
    if os.environ.get(NETWORK_ENV) != "1":
        print(f"refusing: set {NETWORK_ENV}=1 to allow the LLM judge API calls", file=sys.stderr)
        return 2
    from .baselines import AnthropicLLMJudge
    try:
        judge = AnthropicLLMJudge()
        rep = run_gate_judge(lambda cat, j: default_systems(cat, j), judge, est, sets=sets)
    except RuntimeError as e:                      # 認証情報なし・冒頭から連続失敗 → 結果ファイルは書かない
        print(f"gate-judge not run: {e}", file=sys.stderr)
        return 2
    for name, by in rep["systems"].items():
        for tag, r in by.items():
            print(f"{name:18s} {tag} BLOCK-P={r['block_precision']} S1-leak={len(r['s1_leak'])} "
                  f"S2-R={r['s2_recall']} goldPASS={r['gold_pass_rate']}")
    u = rep["judge"]["usage"]
    print(f"judge usage: {u['calls']} calls, {u['input_tokens']:,} in / {u['output_tokens']:,} out tokens, "
          f"${rep['judge']['cost_usd']}, errors={u['errors']} refusals={u['refusals']}; {rep['elapsed_s']} s")
    print("wrote results/gate-judge.md, results/gate-judge.json (gate-v1.* untouched)")
    return 0


def cmd_evaluate(seed_path: Path, seed: int, llm: bool, llm_limit: int | None, sets: list[str] | None = None) -> int:
    from .evaluate import default_systems, evaluate

    cat = Catalog.from_seed(seed_path)
    judge = None
    if llm:
        from .baselines import AnthropicLLMJudge
        judge = AnthropicLLMJudge()
    systems = default_systems(cat, judge)
    if judge is not None and llm_limit:
        systems[-1] = _Limited(systems[-1], llm_limit)
    rep = evaluate(cat, systems, seed=seed, only=sets)
    for name, e in rep["systems"].items():
        for set_name in [k for k in ("auto", "challenge") if k in e]:
            m = e[set_name]["metrics"]
            print(f"{name:18s} {set_name:9s} BLOCK-P={m['block_precision']} S2-R={m['by_severity']['S2']['fail_recall']} "
                  f"R={m['fail_recall']} UNK={m['unknown_rate']}")
    print("wrote results/eval.md, results/eval.json, results/GATE.md")
    return 0


class _Limited:
    """LLM judge の呼び出し回数上限（超えた分は UNKNOWN、コスト管理用）"""

    def __init__(self, inner, limit: int):
        self.inner, self.limit, self.used, self.name = inner, limit, 0, inner.name

    def verify_claim(self, c):
        from .baselines import _finish
        if self.used >= self.limit:
            return _finish(c, "UNKNOWN", "llm_call_limit_reached", None)
        self.used += 1
        return self.inner.verify_claim(c)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.cli")
    ap.add_argument("command", choices=["sources", "build", "evaluate", "all", "gate-v1", "real-pilot", "extract-eval",
                                        "validate-records", "claimwatch-sample", "gate-judge"])
    ap.add_argument("path", nargs="?", type=Path, help="validate-records: 収集記録の JSONL")
    ap.add_argument("--seed-file", type=Path, default=DEFAULT_SEED)
    ap.add_argument("--seed", type=int, default=0, help="mutation の乱数 seed")
    ap.add_argument("--cutoff", default=DEFAULT_TEMPORAL_CUTOFF, help="時間スプリットの基準日")
    ap.add_argument("--llm-judge", action="store_true", help="実キーで LLM judge を回す（要 SPECSURE_ALLOW_NETWORK=1）")
    ap.add_argument("--llm-limit", type=int, default=None, help="LLM judge の最大呼び出し数")
    ap.add_argument("--sets", default=None, help="evaluate の対象セット（例: challenge,probe）。既定は全部")
    ap.add_argument("--force", action="store_true", help="gate-v1 / gate-judge の再実行を許可（通常は使わない）")
    ap.add_argument("--max-calls", type=int, default=200, help="gate-judge: 見積もり呼び出し数がこれを超えたら実行しない")
    ap.add_argument("--estimate-only", action="store_true", help="gate-v1: LLM judge の呼び出し回数と概算コストだけ表示")
    a = ap.parse_args(argv)
    if a.command == "validate-records":
        from .collection import validate_file
        if not a.path:
            ap.error("validate-records needs a JSONL path")
        problems = validate_file(a.path)
        for rid, errs in problems.items():
            print(f"{rid}: " + "; ".join(errs))
        print("OK" if not problems else f"{len(problems)} record(s) failed")
        return 1 if problems else 0
    if a.command == "claimwatch-sample":
        from .claimwatch import write_sample
        print(f"wrote {write_sample().relative_to(ROOT)} (SYNTHETIC)")
        return 0
    if a.command == "extract-eval":
        # Phase 3: extractor v2 の 3 セット評価。v1 の記録（extract-v1*.md/json）は書き換えない。
        # v2 holdout は results/extract-v2-holdout.json が無い時だけ実行する（未見セットは 1 回のみ）。
        from .evaluate import _git_commit_of
        from .extract import evaluate_answers, render_v2_report
        d = ROOT / "data" / "extract"
        sets = {"v1_dev": evaluate_answers(d / "answers_v1.yaml"),
                "v1_holdout": evaluate_answers(d / "answers_holdout_v1.yaml")}
        hold = ROOT / "results" / "extract-v2-holdout.json"
        if hold.exists():
            sets["v2_holdout"] = json.loads(hold.read_text(encoding="utf-8"))
            print("v2 holdout: using the saved single run (not re-run)")
        else:
            sets["v2_holdout"] = evaluate_answers(d / "answers_holdout_v2.yaml")
            hold.write_text(json.dumps(sets["v2_holdout"], ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            print("v2 holdout: ran once and saved results/extract-v2-holdout.json")
        v1_refs = {}
        for key, f in (("v1_dev", "extract-v1.json"), ("v1_holdout", "extract-v1-holdout.json")):
            fp = ROOT / "results" / f
            if fp.exists():
                v1_refs[key] = json.loads(fp.read_text(encoding="utf-8"))
        commits = {"v2_holdout": _git_commit_of(d / "answers_holdout_v2.yaml")}
        (ROOT / "results" / "extract-v2-holdout.md").write_text(render_v2_report(sets, v1_refs, commits), encoding="utf-8")
        for k, r in sets.items():
            print(f"{k:11s} P={r['overall']['precision']} R={r['overall']['recall']} cov={r['coverage']} "
                  f"fp_BLOCK={r['end_to_end'].get('fp_BLOCK', 0)}")
        print("wrote results/extract-v2-holdout.md")
        return 0
    if a.command == "real-pilot":
        from .real_pilot import run as run_pilot
        rep = run_pilot(seed=a.seed)
        print(json.dumps({k: rep[k] for k in ("products", "by_brand", "claims", "leak_issues")}, ensure_ascii=False))
        for name, s_ in rep["systems"].items():
            m = s_["metrics"]
            print(f"{name:18s} BLOCK-P={m['block_precision']} R={m['fail_recall']} goldPASS={m['gold_pass_rate']} "
                  f"UNK={m['unknown_rate']}")
        print(f"per product {rep['timing']['per_product_min']} min (agent); wrote results/real_pilot.md")
        return 0
    if a.command == "gate-judge":
        return cmd_gate_judge(a.force, a.estimate_only, a.max_calls)
    if a.command == "gate-v1":
        return cmd_gate_v1(a.llm_judge, a.force, a.estimate_only)
    if a.command == "sources":
        print(write_sources_md())
        return 0
    if a.command in ("build", "all"):
        rc = cmd_build(a.seed_file.resolve(), a.seed, a.cutoff)
        if rc:
            return rc
    if a.command in ("evaluate", "all"):
        return cmd_evaluate(a.seed_file.resolve(), a.seed, a.llm_judge, a.llm_limit,
                            a.sets.split(",") if a.sets else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
