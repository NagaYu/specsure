"""評価ハーネス: 評価セットを各判定器に通し、指標と類型別テーブルを results/ に出す。GATE.md も生成する。

評価セット
  auto      = gold（正解 PASS）+ mutation（正解 FAIL, 類型ラベル付き）… 生成器と verifier が同じ語彙・許容誤差を共有するため上限寄り
  challenge = 手書き（語彙外の真の主張 PASS + 現実的な誤り FAIL）… 循環しない検査
  probe     = 証拠なしプローブ（正解 UNKNOWN）… 推測しないことの検査。precision / recall には含めない
"""

from __future__ import annotations

import json
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

from .claims import Claim, challenge_claims, gold_claims, probe_claims
from .mutate import ERROR_TYPES, mutations
from .normalize import ROOT
from .verify import Verifier

RESULTS_DIR = ROOT / "results"
ALL_ERROR_TYPES = ("E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8")


def eval_sets(catalog, seed: int = 0) -> dict[str, list[Claim]]:
    return {
        "auto": gold_claims(catalog) + mutations(catalog, seed=seed),
        "challenge": challenge_claims(catalog),
        "probe": probe_claims(catalog),
    }


def run_system(system, claims: Iterable[Claim]) -> tuple[list, list[float]]:
    results, times = [], []
    for c in claims:
        t0 = time.perf_counter()
        r = system.verify_claim(c)
        times.append(time.perf_counter() - t0)
        results.append(r)
    return results, times


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 4) if b else None


def metrics(claims: list[Claim], results: list, times: list[float]) -> dict:
    pairs = list(zip(claims, results))
    labeled = [(c, r) for c, r in pairs if c.label["expected"] in ("PASS", "FAIL")]
    errs = [(c, r) for c, r in labeled if c.label["expected"] == "FAIL"]
    golds = [(c, r) for c, r in labeled if c.label["expected"] == "PASS"]
    pred_fail = [(c, r) for c, r in labeled if r.verdict == "FAIL"]
    pred_block = [(c, r) for c, r in labeled if r.outcome == "BLOCK"]
    m: dict = {
        "n_claims": len(pairs),
        "n_errors": len(errs),
        "n_gold": len(golds),
        "verdicts": dict(Counter(r.verdict for _, r in pairs)),
        "fail_precision": _ratio(sum(c.label["expected"] == "FAIL" for c, _ in pred_fail), len(pred_fail)),
        "block_precision": _ratio(sum(c.label["expected"] == "FAIL" for c, _ in pred_block), len(pred_block)),
        "n_block": len(pred_block),
        "fail_recall": _ratio(sum(r.verdict == "FAIL" for _, r in errs), len(errs)),
        "caught_recall": _ratio(sum(r.outcome in ("BLOCK", "WARN") for _, r in errs), len(errs)),
        "false_fail_rate_on_gold": _ratio(sum(r.verdict == "FAIL" for _, r in golds), len(golds)),
        "false_block_rate_on_gold": _ratio(sum(r.outcome == "BLOCK" for _, r in golds), len(golds)),
        "gold_pass_rate": _ratio(sum(r.verdict == "PASS" for _, r in golds), len(golds)),
        "unknown_rate": _ratio(sum(r.verdict == "UNKNOWN" for _, r in pairs), len(pairs)),
        "unknown_rate_gold": _ratio(sum(r.verdict == "UNKNOWN" for _, r in golds), len(golds)),
        "unknown_rate_errors": _ratio(sum(r.verdict == "UNKNOWN" for _, r in errs), len(errs)),
        "s1_error_passed": [c.claim_id for c, r in errs if r.severity == "S1" and r.verdict == "PASS"],
    }
    by_sev = {}
    for sev in ("S1", "S2", "S3"):
        e = [(c, r) for c, r in errs if c.severity == sev]
        by_sev[sev] = {"n": len(e),
                       "fail_recall": _ratio(sum(r.verdict == "FAIL" for _, r in e), len(e)),
                       "caught_recall": _ratio(sum(r.outcome in ("BLOCK", "WARN") for _, r in e), len(e)),
                       "block_recall": _ratio(sum(r.outcome == "BLOCK" for _, r in e), len(e))}
    m["by_severity"] = by_sev
    by_type = {}
    for et in ALL_ERROR_TYPES:
        e = [(c, r) for c, r in errs if c.label.get("error_type") == et]
        # 診断は FAIL に加え、S1 の不在主張を UNKNOWN で捕捉した場合（kind=unverified_risk）も数える
        diag = [r for _, r in e if getattr(r, "diagnosis", None)]
        by_type[et] = {"n": len(e),
                       "fail_recall": _ratio(sum(r.verdict == "FAIL" for _, r in e), len(e)),
                       "caught_recall": _ratio(sum(r.outcome in ("BLOCK", "WARN") for _, r in e), len(e)),
                       "unknown": sum(r.verdict == "UNKNOWN" for _, r in e),
                       "diagnosis_accuracy": _ratio(sum(r.diagnosis["error_type"] == et for r in diag), len(diag))}
    m["by_error_type"] = by_type
    resolved = [(c, r) for c, r in labeled if r.product_id is not None]
    m["identity_resolved_rate"] = _ratio(len(resolved), len(labeled))
    rf = [(c, r) for c, r in resolved if r.verdict == "FAIL"]
    m["fail_precision_given_identity"] = _ratio(sum(c.label["expected"] == "FAIL" for c, _ in rf), len(rf))
    re_ = [(c, r) for c, r in resolved if c.label["expected"] == "FAIL"]
    m["fail_recall_given_identity"] = _ratio(sum(r.verdict == "FAIL" for _, r in re_), len(re_))
    us = sorted(t * 1e6 for t in times) or [0.0]
    m["timing"] = {"total_ms": round(sum(times) * 1e3, 2), "mean_us": round(statistics.mean(us), 1),
                   "p95_us": round(us[int(0.95 * (len(us) - 1))], 1)}
    return m


def failure_cases(claims: list[Claim], results: list, limit: int = 15) -> dict:
    misses, false_alarms, unknown_gold = [], [], []
    for c, r in zip(claims, results):
        row = {"claim_id": c.claim_id, "product_id": c.product_id, "type": c.claim_type, "attribute": c.attribute,
               "polarity": c.polarity, "value": c.value, "unit": c.unit, "severity": r.severity,
               "expected": c.label["expected"], "error_type": c.label.get("error_type"), "verdict": r.verdict,
               "outcome": r.outcome, "reason": r.reason,
               "note": (c.label.get("rationale") or {}).get("note") or (c.label.get("rationale") or {}).get("method")}
        if c.label["expected"] == "FAIL" and r.verdict != "FAIL":
            misses.append(row)
        elif c.label["expected"] == "PASS" and r.verdict == "FAIL":
            false_alarms.append(row)
        elif c.label["expected"] == "PASS" and r.verdict == "UNKNOWN":
            unknown_gold.append(row)
    key = {"S1": 0, "S2": 1, "S3": 2}
    misses.sort(key=lambda x: key[x["severity"]])
    false_alarms.sort(key=lambda x: key[x["severity"]])
    return {"misses": misses[:limit], "n_misses": len(misses), "false_alarms": false_alarms[:limit],
            "n_false_alarms": len(false_alarms), "unknown_on_true_claims": unknown_gold[:limit],
            "n_unknown_on_true_claims": len(unknown_gold)}


def probe_report(claims: list[Claim], results: list) -> dict:
    by_kind = defaultdict(lambda: Counter())
    for c, r in zip(claims, results):
        by_kind[c.label["rationale"]["probe"]][r.verdict] += 1
    s1 = [(c, r) for c, r in zip(claims, results) if r.severity == "S1"]
    return {"n": len(claims), "unknown_rate": _ratio(sum(r.verdict == "UNKNOWN" for r in results), len(results)),
            "by_kind": {k: dict(v) for k, v in sorted(by_kind.items())},
            "s1_probes_blocked": _ratio(sum(r.outcome == "BLOCK" for _, r in s1), len(s1)), "n_s1": len(s1)}


# ───────────────────────────── 実行と出力 ─────────────────────────────

def evaluate(catalog, systems: list, seed: int = 0, out_dir: Path = RESULTS_DIR, only: list[str] | None = None) -> dict:
    sets = eval_sets(catalog, seed)
    if only:
        sets = {k: v for k, v in sets.items() if k in only}
    report: dict = {"seed": seed, "n_products": len(catalog.products),
                    "sets": {k: {"n": len(v), "expected": dict(Counter(c.label["expected"] for c in v))}
                             for k, v in sets.items()},
                    "systems": {}}
    for sys_ in systems:
        entry = {}
        for name, claims in sets.items():
            results, times = run_system(sys_, claims)
            if name == "probe":
                entry[name] = probe_report(claims, results)
            else:
                entry[name] = {"metrics": metrics(claims, results, times),
                               "failures": failure_cases(claims, results)}
        report["systems"][sys_.name] = entry
    out_dir.mkdir(parents=True, exist_ok=True)
    if only:                                   # 部分実行は JSON だけ（eval.md / GATE.md は全セット前提）
        (out_dir / f"eval-{'-'.join(sorted(sets))}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
        return report
    (out_dir / "eval.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str) + "\n",
                                       encoding="utf-8")
    (out_dir / "eval.md").write_text(render_eval_md(report), encoding="utf-8")
    (out_dir / "GATE.md").write_text(render_gate_md(report), encoding="utf-8")
    return report


def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def render_eval_md(rep: dict) -> str:
    L = ["# SpecBench v0 — verifier 評価結果", "",
         f"seed={rep['seed']} / 商品数 {rep['n_products']} / 生成: `python -m src.cli evaluate`", "",
         "| 評価セット | claim 数 | 正解内訳 |", "|---|---|---|"]
    for k, v in rep["sets"].items():
        L.append(f"| {k} | {v['n']} | {v['expected']} |")
    for sname, entry in rep["systems"].items():
        L += ["", f"## {sname}", ""]
        for set_name in ("auto", "challenge"):
            m = entry[set_name]["metrics"]
            L += [f"### {set_name}", "",
                  "| 指標 | 値 |", "|---|---|",
                  f"| BLOCK precision | {_pct(m['block_precision'])} (BLOCK {m['n_block']} 件) |",
                  f"| FAIL precision | {_pct(m['fail_precision'])} |",
                  f"| FAIL recall（誤り claim） | {_pct(m['fail_recall'])} |",
                  f"| 検出 recall（BLOCK/WARN） | {_pct(m['caught_recall'])} |",
                  f"| gold の誤 FAIL 率 | {_pct(m['false_fail_rate_on_gold'])} |",
                  f"| gold の誤 BLOCK 率 | {_pct(m['false_block_rate_on_gold'])} |",
                  f"| UNKNOWN 率（全体 / gold / 誤り） | {_pct(m['unknown_rate'])} / {_pct(m['unknown_rate_gold'])} / "
                  f"{_pct(m['unknown_rate_errors'])} |",
                  f"| 同定成功率 | {_pct(m['identity_resolved_rate'])} |",
                  f"| S1 の誤りを PASS した件数 | {len(m['s1_error_passed'])} |",
                  f"| 処理時間 | 合計 {m['timing']['total_ms']} ms / 平均 {m['timing']['mean_us']} µs / "
                  f"p95 {m['timing']['p95_us']} µs |", "",
                  "| 重大度 | n | FAIL recall | BLOCK/WARN recall | BLOCK recall |", "|---|---|---|---|---|"]
            for sev, s in m["by_severity"].items():
                L.append(f"| {sev} | {s['n']} | {_pct(s['fail_recall'])} | {_pct(s['caught_recall'])} | "
                         f"{_pct(s['block_recall'])} |")
            L += ["", "| 類型 | n | FAIL recall | BLOCK/WARN recall | UNKNOWN | 診断一致率 |", "|---|---|---|---|---|---|"]
            for et, s in m["by_error_type"].items():
                if et == "E6":
                    L.append("| E6 | — | 対象外（Phase 1） | — | — | — |")
                    continue
                L.append(f"| {et} | {s['n']} | {_pct(s['fail_recall'])} | {_pct(s['caught_recall'])} | "
                         f"{s['unknown']} | {_pct(s['diagnosis_accuracy'])} |")
            L.append("")
        p = entry["probe"]
        L += ["### probe（正解 UNKNOWN）", "", f"UNKNOWN 率 {_pct(p['unknown_rate'])}（{p['n']} 件）、"
              f"S1 プローブの BLOCK 率 {_pct(p['s1_probes_blocked'])}（{p['n_s1']} 件）", "",
              "| 種別 | 判定内訳 |", "|---|---|"]
        for k, v in p["by_kind"].items():
            L.append(f"| {k} | {v} |")
    return "\n".join(L) + "\n"


def _cases_table(rows: list[dict]) -> list[str]:
    if not rows:
        return ["（なし）", ""]
    out = ["| claim | 商品 | 種別 | 値 | 重大度 | 正解 | 判定 | 理由 | メモ |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        val = f"{'NOT ' if r['polarity'] == 'deny' else ''}{r['value']}{' ' + r['unit'] if r['unit'] else ''}"
        out.append(f"| `{r['claim_id']}` | {r['product_id']} | {r['type']}/{r['attribute']} | {val} | {r['severity']} | "
                   f"{r['expected']}{'/' + r['error_type'] if r['error_type'] else ''} | {r['verdict']}→{r['outcome']} | "
                   f"{r['reason']} | {r['note'] or ''} |")
    return out + [""]


def render_gate_md(rep: dict) -> str:
    sys_ = rep["systems"]
    det, naive = sys_.get("deterministic_v1"), sys_.get("naive_matcher")
    llm = {k: v for k, v in sys_.items() if k.startswith("llm_judge")}
    L = ["# Week 2 ゲート判定材料（自動生成）", "",
         "> このファイルは `python -m src.cli evaluate` が生成する。**結論は書かない**。Go/No-Go の判断は Yuta が行う。",
         f"> 数字は SpecBench v0（{rep['n_products']} 商品・全て合成データ、mutation seed={rep['seed']}）上のもので、"
         "実エージェント回答・実在商品での性能を意味しない。", "",
         "## 1. 決定論 verifier と naive matcher の差", ""]
    rows = [("BLOCK precision", "block_precision"), ("FAIL precision", "fail_precision"),
            ("FAIL recall（全誤り）", "fail_recall"), ("gold の誤 BLOCK 率", "false_block_rate_on_gold"),
            ("UNKNOWN 率", "unknown_rate"), ("同定成功率", "identity_resolved_rate"),
            ("FAIL precision（同定できた claim のみ）", "fail_precision_given_identity"),
            ("FAIL recall（同定できた claim のみ）", "fail_recall_given_identity")]
    for set_name, title in (("auto", "auto（gold + mutation、生成器と verifier が定義を共有＝上限寄り）"),
                            ("challenge", "challenge（手書き、語彙外の言い回しと現実的な誤り）")):
        dm, nm = det[set_name]["metrics"], naive[set_name]["metrics"]
        L += [f"### {title}", "", "| 指標 | 決定論 v1 | naive | 差（v1 − naive） |", "|---|---|---|---|"]
        for label, key in rows:
            a, b = dm.get(key), nm.get(key)
            diff = "—" if a is None or b is None else f"{(a - b) * 100:+.1f} pt"
            L.append(f"| {label} | {_pct(a)} | {_pct(b)} | {diff} |")
        for sev in ("S1", "S2", "S3"):
            a, b = dm["by_severity"][sev], nm["by_severity"][sev]
            diff = ("—" if a["fail_recall"] is None or b["fail_recall"] is None
                    else f"{(a['fail_recall'] - b['fail_recall']) * 100:+.1f} pt")
            L.append(f"| {sev} recall（FAIL, n={a['n']}） | {_pct(a['fail_recall'])} | {_pct(b['fail_recall'])} | {diff} |")
        a, b = dm["by_severity"]["S1"], nm["by_severity"]["S1"]
        L.append(f"| S1 BLOCK recall（UNKNOWN→BLOCK 含む） | {_pct(a['block_recall'])} | {_pct(b['block_recall'])} | "
                 f"{'—' if a['block_recall'] is None or b['block_recall'] is None else f'{(a['block_recall'] - b['block_recall']) * 100:+.1f} pt'} |")
        L.append(f"| S1 の誤りを PASS した件数 | {len(dm['s1_error_passed'])} | {len(nm['s1_error_passed'])} | |")
        L.append(f"| 平均処理時間 / claim | {dm['timing']['mean_us']} µs | {nm['timing']['mean_us']} µs | |")
        L.append("")
    dp, np_ = det["probe"], naive["probe"]
    L += ["### probe（スナップショットから判断できない主張、正解 UNKNOWN）", "",
          f"- 決定論 v1: UNKNOWN 率 {_pct(dp['unknown_rate'])}、S1 プローブの BLOCK 率 {_pct(dp['s1_probes_blocked'])}",
          f"- naive: UNKNOWN 率 {_pct(np_['unknown_rate'])}、S1 プローブの BLOCK 率 {_pct(np_['s1_probes_blocked'])}", "",
          "## 2. LLM judge", ""]
    if llm:
        for name, e in llm.items():
            for set_name in ("auto", "challenge"):
                m = e[set_name]["metrics"]
                L.append(f"- {name} / {set_name}: BLOCK precision {_pct(m['block_precision'])}、"
                         f"S2 recall {_pct(m['by_severity']['S2']['fail_recall'])}、"
                         f"FAIL recall {_pct(m['fail_recall'])}、gold 誤 BLOCK {_pct(m['false_block_rate_on_gold'])}、"
                         f"UNKNOWN {_pct(m['unknown_rate'])}、平均 {m['timing']['mean_us'] / 1000:.0f} ms/claim")
        L.append("")
    else:
        L += ["**実測なし。** LLM judge は interface とモックのみ実装済み。実キーでの実行手順は README「LLM judge を実キーで回す」を参照。", ""]
    L += ["## 3. 失敗事例（決定論 v1）", ""]
    for set_name in ("challenge", "auto"):
        f = det[set_name]["failures"]
        L += [f"### {set_name}: 誤りの見逃し（正解 FAIL → FAIL 以外）{f['n_misses']} 件", ""] + _cases_table(f["misses"])
        L += [f"### {set_name}: 誤検知（正解 PASS → FAIL）{f['n_false_alarms']} 件", ""] + _cases_table(f["false_alarms"])
        L += [f"### {set_name}: 真の主張への UNKNOWN {f['n_unknown_on_true_claims']} 件", ""] + \
            _cases_table(f["unknown_on_true_claims"])
    L += ["## 4. naive matcher の代表的な失敗（challenge）", ""]
    f = naive["challenge"]["failures"]
    L += [f"誤検知 {f['n_false_alarms']} 件 / 見逃し {f['n_misses']} 件", ""] + _cases_table(f["false_alarms"][:8])
    L += ["## 5. 数字を読む際の注意（判断材料）", "",
          "- **auto セットは循環的**: gold と mutation は verifier と同じ語彙（vocab.yaml）・許容誤差（tolerances.yaml）から"
          "生成しているため、決定論 v1 の auto 指標は実質的に上限値。差を見る主材料は challenge と probe。",
          "- **challenge セットは 60 件と小さい**: 1 件で 1.5〜3 pt 動く。信頼区間は広い。",
          "- **challenge（v0）は開発セット**: Phase 2 Step 0 で v0 の失敗事例を見て弱点修正した（色の同義語、ブランドなし型番、"
          "表記精度フロア等）。v0 の数字は上振れしている。未見の評価は results/gate-v1.md（challenge v1）を参照。",
          "- **naive matcher の同定は文字列完全一致**: 表記揺れ（全角・GTIN-14・小文字）を含む claim は同定できず UNKNOWN になる。"
          "「同定できた claim のみ」の行は照合部分だけの比較。",
          "- **集約ポリシー（Phase 2）**: S1 の UNKNOWN は BLOCK、S2 の UNKNOWN は WARN、S3 の UNKNOWN は PASS（`unverified_claims` に列挙）。",
          "- 全て合成データ（架空ブランド・GS1 店内用 GTIN）。実在メーカー仕様は規約判断待ちで未取り込み（docs/SOURCES.md）。", ""]
    return "\n".join(L) + "\n"


def default_systems(catalog, llm_judge=None) -> list:
    from .baselines import LLMJudgeBaseline, NaiveMatcher

    det = Verifier(catalog)
    det.name = "deterministic_v1"
    systems = [det, NaiveMatcher(catalog)]
    if llm_judge is not None:
        systems.append(LLMJudgeBaseline(catalog, llm_judge))
    return systems


# ───────────────────────────── Gate v1（未見 challenge v1 + 開発セット v0） ─────────────────────────────

GATE_V1_JSON = RESULTS_DIR / "gate-v1.json"
LLM_PRICE_PER_MTOK = {"claude-opus-5-5": (4.00, 20.00), "claude-sonnet-5-5": (2.00, 10.00),
                      "claude-haiku-5-5": (0.10, 0.50)}
EST_OUTPUT_TOKENS = 400          # JSON 判定 + effort low の思考を見込んだ概算


def _git_commit_of(path: Path) -> str | None:
    import subprocess
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%H %cI", "--", str(path)], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.strip()
        return out or None
    except Exception:
        return None


def challenge_sets() -> dict[str, tuple]:
    """{"v0": (catalog, claims), "v1": (catalog, claims)}"""
    from .claims import CHALLENGE_V0, CHALLENGE_V1, challenge_catalog

    out = {}
    for tag, name in (("v0", CHALLENGE_V0), ("v1", CHALLENGE_V1)):
        path = ROOT / "data" / "seed" / name
        if not path.exists():                  # 公開ツリーには未見 holdout（v1）を含めない
            continue
        cat = challenge_catalog(path)
        out[tag] = (cat, challenge_claims(cat, path))
    return out


def llm_call_estimate(sets: dict, model: str) -> dict:
    """LLM judge の呼び出し回数（同定できた claim だけ呼ぶ）と概算コスト"""
    from .baselines import JUDGE_SYSTEM

    calls, in_chars = 0, 0
    for cat, claims in sets.values():
        for c in claims:
            ref = c.product_ref or {}
            res = cat.identity.resolve(gtin=ref.get("gtin"), model=ref.get("model_number"), name=ref.get("name"))
            if not res.ok:
                continue
            calls += 1
            snap = {"product": {k: cat.products[res.product_id].get(k)
                                for k in ("model_number", "brand", "category", "region", "variant_axes")},
                    **cat.snapshots[res.product_id]}
            in_chars += len(JUDGE_SYSTEM) + len(json.dumps(snap, ensure_ascii=False)) + \
                len(json.dumps(c.verifier_view(), ensure_ascii=False)) + 40
    in_tok = int(in_chars / 2.5)                 # 日英混在 JSON の概算（2.5 文字/トークン）
    out_tok = calls * EST_OUTPUT_TOKENS
    pin, pout = LLM_PRICE_PER_MTOK.get(model, LLM_PRICE_PER_MTOK["claude-opus-5-5"])
    return {"model": model, "calls": calls, "claims": sum(len(c) for _, c in sets.values()),
            "est_input_tokens": in_tok, "est_output_tokens": out_tok,
            "est_cost_usd": round(in_tok / 1e6 * pin + out_tok / 1e6 * pout, 2)}


def gate_row(claims: list[Claim], results: list) -> dict:
    m = metrics(claims, results, [])
    errs = [(c, r) for c, r in zip(claims, results) if c.label["expected"] == "FAIL"]
    s1_leak = [c.claim_id for c, r in errs if c.severity == "S1" and r.outcome != "BLOCK"]
    return {"block_precision": m["block_precision"], "n_block": m["n_block"], "s1_leak": s1_leak,
            "s1_errors": m["by_severity"]["S1"]["n"], "s2_recall": m["by_severity"]["S2"]["fail_recall"],
            "n_s2": m["by_severity"]["S2"]["n"], "gold_pass_rate": m["gold_pass_rate"],
            "fail_precision": m["fail_precision"], "fail_recall": m["fail_recall"],
            "false_block_rate_on_gold": m["false_block_rate_on_gold"], "unknown_rate": m["unknown_rate"],
            "by_error_type": {k: v["fail_recall"] for k, v in m["by_error_type"].items()}}


def run_gate_v1(make_systems, llm_status: str, out_dir: Path = RESULTS_DIR, sets: dict | None = None) -> dict:
    """make_systems(catalog) → [system...]。結果は gate-v1.{md,json}。sets はテスト用の差し替え。"""
    sets = sets or challenge_sets()
    v1_path = ROOT / "data" / "seed" / "challenge_v1.yaml"
    rep = {"challenge_v1_commit": _git_commit_of(v1_path),
           "challenge_v1_products_commit": _git_commit_of(ROOT / "data" / "seed" / "challenge_v1_products.yaml"),
           "run_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "llm_judge": llm_status,
           "sets": {k: {"n": len(c), "expected": dict(Counter(x.label["expected"] for x in c))}
                    for k, (_, c) in sets.items()},
           "systems": {}, "failures_v1": []}
    for tag, (cat, claims) in sets.items():
        for sys_ in make_systems(cat):
            results, _ = run_system(sys_, claims)
            rep["systems"].setdefault(sys_.name, {})[tag] = gate_row(claims, results)
            if tag == "v1" and sys_.name == "deterministic_v1":
                for c, r in zip(claims, results):
                    if r.verdict != c.label["expected"]:
                        rep["failures_v1"].append({
                            "claim_id": c.claim_id, "product_id": c.product_id, "type": c.claim_type,
                            "attribute": c.attribute, "polarity": c.polarity, "value": c.value, "unit": c.unit,
                            "severity": r.severity, "expected": c.label["expected"],
                            "error_type": c.label.get("error_type"), "verdict": r.verdict, "outcome": r.outcome,
                            "reason": r.reason, "note": c.label["rationale"].get("note", ""),
                            "backlog": backlog_class(c, r)})
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "gate-v1.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str) + "\n",
                                          encoding="utf-8")
    (out_dir / "gate-v1.md").write_text(render_gate_v1_md(rep), encoding="utf-8")
    return rep


def backlog_class(c: Claim, r) -> str:
    """Phase 3 バックログの分類（理由コードからの機械的な分類。個別の判断は gate-v1.md を見て人が行う）"""
    reason = r.reason or ""
    if reason.startswith("unknown_unit") or reason == "unparseable_value":
        return "数値・単位表記の解析（範囲・複合単位・助数詞など）"
    if reason.startswith("compat_item_ambiguous") or reason == "not_in_non_exhaustive_list":
        return "互換性項目の表記・網羅性（非網羅リスト・規格表記）"
    if reason in ("not_in_exhaustive_list",) and c.label["expected"] == "PASS":
        return "互換性項目の表記揺れ（網羅リストで誤検知）"
    if c.claim_type == "variant":
        return "バリアント値の語彙・換算（香り・サイズ表記・対応機種軸）"
    if c.claim_type == "bundle":
        return "同梱物の語彙"
    if c.claim_type == "ingredient":
        return "成分語彙・不在判定"
    if reason == "out_of_tolerance":
        return "許容誤差・丸め"
    if not c.label.get("evidence_in_snapshot", True):
        return "スナップショットに証拠なし（設計どおり UNKNOWN）"
    return "その他"


def render_gate_v1_md(rep: dict) -> str:
    L = ["# Gate v1 — 未見 challenge v1 による測り直し（自動生成）", "",
         "> `python -m src.cli gate-v1` が生成。**結論は書かない**（ゲート判断は Yuta）。全て合成データ。", "",
         f"- challenge v1 の事前コミット: `{rep['challenge_v1_commit']}`",
         f"- v1 カタログ拡張の事前コミット: `{rep['challenge_v1_products_commit']}`",
         f"- 実行日時: {rep['run_at']}（v1 の実行はこの 1 回のみ。結果を見た後の調整はしていない）",
         f"- LLM judge: {rep['llm_judge']}", "",
         "| セット | claim 数 | 内訳 | 位置づけ |", "|---|---|---|---|",
         f"| v0 | {rep['sets']['v0']['n']} | {rep['sets']['v0']['expected']} | 開発セット（Step 0 で失敗事例を見て修正済み＝上振れ） |",
         f"| v1 | {rep['sets']['v1']['n']} | {rep['sets']['v1']['expected']} | 未見（コミット後に 1 回だけ実行） |", "",
         "## 方式比較", "",
         "| 方式 | セット | BLOCK precision | S1 素通し（BLOCK されなかった S1 誤り） | S2 recall | gold PASS 率 | FAIL precision | FAIL recall | gold 誤 BLOCK 率 | UNKNOWN 率 |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for name, by in rep["systems"].items():
        for tag in ("v0", "v1"):
            r = by.get(tag)
            if not r:
                continue
            L.append(f"| {name} | {tag} | {_pct(r['block_precision'])} (n={r['n_block']}) | "
                     f"{len(r['s1_leak'])} / {r['s1_errors']} | {_pct(r['s2_recall'])} (n={r['n_s2']}) | "
                     f"{_pct(r['gold_pass_rate'])} | {_pct(r['fail_precision'])} | {_pct(r['fail_recall'])} | "
                     f"{_pct(r['false_block_rate_on_gold'])} | {_pct(r['unknown_rate'])} |")
    L += ["", "S1 素通しの claim: " + "; ".join(f"{n}/{t}: {', '.join(r[t]['s1_leak']) or 'なし'}"
                                        for n, r in rep["systems"].items() for t in ("v0", "v1") if t in r), "",
          "## 類型別 FAIL recall（v1）", "", "| 方式 | " + " | ".join(ALL_ERROR_TYPES) + " |",
          "|---|" + "---|" * len(ALL_ERROR_TYPES)]
    for name, by in rep["systems"].items():
        if "v1" in by:
            L.append(f"| {name} | " + " | ".join(_pct(by["v1"]["by_error_type"].get(e)) for e in ALL_ERROR_TYPES) + " |")
    fails = rep["failures_v1"]
    L += ["", f"## Phase 3 バックログ（決定論 v1 の v1 失敗 {len(fails)} 件、分類のみ・この Phase では修正しない）", ""]
    groups = Counter(f["backlog"] for f in fails)
    L += ["| 分類 | 件数 |", "|---|---|"] + [f"| {k} | {v} |" for k, v in groups.most_common()] + [""]
    L += ["| claim | 商品 | 種別 | 値 | 重大度 | 正解 | 判定 | 理由 | 分類 | メモ |", "|---|---|---|---|---|---|---|---|---|---|"]
    for f in sorted(fails, key=lambda x: (x["backlog"], x["claim_id"])):
        val = f"{'NOT ' if f['polarity'] == 'deny' else ''}{f['value']}{' ' + f['unit'] if f['unit'] else ''}"
        L.append(f"| `{f['claim_id']}` | {f['product_id']} | {f['type']}/{f['attribute']} | {val} | {f['severity']} | "
                 f"{f['expected']}{'/' + f['error_type'] if f['error_type'] else ''} | {f['verdict']}→{f['outcome']} | "
                 f"{f['reason']} | {f['backlog']} | {f['note']} |")
    L += ["", "## 読む際の注意", "",
          "- v1 は 120 件（誤り 60）。1 件で約 1.7 pt（誤り内では約 1.7 pt、類型内では 11–13 pt）動く。",
          "- v1 の作成者は verifier の実装を知っている（完全な盲検ではない）。",
          "- naive matcher の同定は文字列完全一致。v1 では product_ref の表記揺れ（小文字・全角・ブランド付き・GTIN-14）を含む。",
          "- 集約ポリシー: S1 UNKNOWN→BLOCK、S2 UNKNOWN→WARN、S3 UNKNOWN→PASS。", ""]
    return "\n".join(L) + "\n"


# ───────────────────────────── Gate judge（LLM judge 比較。gate-v1.md とは別ファイル） ─────────────────────────────

GATE_JUDGE_JSON = RESULTS_DIR / "gate-judge.json"


def judge_cost_usd(usage: dict, model: str) -> float:
    pin, pout = LLM_PRICE_PER_MTOK.get(model, LLM_PRICE_PER_MTOK["claude-opus-5-5"])
    return round(usage["input_tokens"] / 1e6 * pin + usage["output_tokens"] / 1e6 * pout, 4)


def run_gate_judge(make_systems, judge, estimate: dict, out_dir: Path = RESULTS_DIR,
                   sets: dict | None = None) -> dict:
    """challenge v0+v1 で決定論 / naive / LLM judge を比較し gate-judge.{md,json} に書く（gate-v1.* は触らない）。
    make_systems(catalog, judge) → [system...]。judge の usage（実呼び出し数・トークン・所要時間）を記録する。"""
    sets = sets or challenge_sets()
    t0 = time.perf_counter()
    rep = {"run_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "challenge_v1_commit": _git_commit_of(ROOT / "data" / "seed" / "challenge_v1.yaml"),
           "judge": {"name": judge.name, "model": judge.model, "effort": judge.effort,
                     "mock": bool(getattr(judge, "is_mock", False))},
           "estimate": estimate,
           "sets": {k: {"n": len(c), "expected": dict(Counter(x.label["expected"] for x in c))}
                    for k, (_, c) in sets.items()},
           "systems": {}, "judge_disagreements": []}
    for tag, (cat, claims) in sets.items():
        for sys_ in make_systems(cat, judge):
            results, _ = run_system(sys_, claims)
            rep["systems"].setdefault(sys_.name, {})[tag] = gate_row(claims, results)
            if sys_.name == judge.name:
                for c, r in zip(claims, results):
                    if r.verdict != c.label["expected"]:
                        rep["judge_disagreements"].append({
                            "set": tag, "claim_id": c.claim_id, "type": c.claim_type, "polarity": c.polarity,
                            "value": c.value, "unit": c.unit, "severity": r.severity,
                            "expected": c.label["expected"], "verdict": r.verdict, "outcome": r.outcome,
                            "reason": r.reason})
    usage = dict(judge.usage)
    rep["judge"]["usage"] = usage
    rep["judge"]["cost_usd"] = judge_cost_usd(usage, judge.model)
    rep["elapsed_s"] = round(time.perf_counter() - t0, 1)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "gate-judge.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str) + "\n",
                                             encoding="utf-8")
    (out_dir / "gate-judge.md").write_text(render_gate_judge_md(rep), encoding="utf-8")
    return rep


def render_gate_judge_md(rep: dict) -> str:
    j, u, est = rep["judge"], rep["judge"]["usage"], rep["estimate"]
    L = ["# Gate judge — LLM judge 比較（自動生成）", ""]
    if j["mock"]:
        L += ["> **MOCK — 実測ではない。** テスト用のモック judge による出力。実キーでの実行結果ではない。", ""]
    L += ["> `python -m src.cli gate-judge` が生成。**結論は書かない**（ゲート判断は Yuta）。全て合成データ。",
          "> results/gate-v1.md（v1 を 1 回だけ実行した記録）は書き換えていない。決定論 / naive の数値はここで再計算したもの。", "",
          f"- 実行日時: {rep['run_at']}、所要時間 {rep['elapsed_s']} 秒（judge 呼び出し {u['seconds']:.1f} 秒）",
          f"- challenge v1 のコミット: `{rep['challenge_v1_commit']}`",
          f"- judge: `{j['name']}`、モデル `{j['model']}`、effort `{j['effort']}`、サーバ側フォールバック `fallbacks=default`",
          f"- 実行前の見積もり: {est.get('calls')} 回、入力 ~{est.get('est_input_tokens', 0):,} / 出力 ~{est.get('est_output_tokens', 0):,} "
          f"トークン、~${est.get('est_cost_usd')}",
          f"- **実測**: 呼び出し {u['calls']} 回（API エラー {u['errors']}、拒否 {u['refusals']}、JSON 解析失敗 {u['unparseable']}）、"
          f"入力 {u['input_tokens']:,} / 出力 {u['output_tokens']:,} トークン、**${j['cost_usd']}**"
          f"（{j['model']} の公開単価で計算。フォールバック先が別モデルの場合は単価が異なる）",
          f"- 応答したモデル: {u['served_models']}", "",
          "| 方式 | セット | BLOCK precision | S1 素通し | S2 recall | gold PASS 率 | FAIL precision | FAIL recall | gold 誤 BLOCK 率 | UNKNOWN 率 |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for name, by in rep["systems"].items():
        for tag in ("v0", "v1"):
            r = by.get(tag)
            if r:
                L.append(f"| {name} | {tag} | {_pct(r['block_precision'])} (n={r['n_block']}) | "
                         f"{len(r['s1_leak'])} / {r['s1_errors']} | {_pct(r['s2_recall'])} (n={r['n_s2']}) | "
                         f"{_pct(r['gold_pass_rate'])} | {_pct(r['fail_precision'])} | {_pct(r['fail_recall'])} | "
                         f"{_pct(r['false_block_rate_on_gold'])} | {_pct(r['unknown_rate'])} |")
    dis = rep["judge_disagreements"]
    L += ["", f"## judge が正解と食い違った claim（{len(dis)} 件）", "",
          "| セット | claim | 種別 | 値 | 重大度 | 正解 | 判定 | 理由 |", "|---|---|---|---|---|---|---|---|"]
    for d in dis:
        val = f"{'NOT ' if d['polarity'] == 'deny' else ''}{d['value']}{' ' + d['unit'] if d['unit'] else ''}"
        L.append(f"| {d['set']} | `{d['claim_id']}` | {d['type']} | {val} | {d['severity']} | {d['expected']} | "
                 f"{d['verdict']}→{d['outcome']} | {str(d['reason'])[:120]} |")
    L += ["", "## 読む際の注意", "",
          "- v0 は開発セット（Phase 2 Step 0 で決定論 verifier を調整済み）。v1 は未見だが作成者は verifier の実装を知っている。",
          "- judge には決定論 verifier と同じ同定結果のスナップショットを渡している（照合部分だけの比較）。",
          "- LLM の出力は非決定的。同じ設定で再実行すると数値が変わりうる。", ""]
    return "\n".join(L) + "\n"
