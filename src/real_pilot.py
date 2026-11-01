"""real_pilot: 実在メーカー公式ページから手作業で属性化した 30 商品の取り込み・評価・取り込み時間の集計。

合成 seed（SpecBench v0）とは別マニフェスト（data/real_pilot/manifests/）。結果も別表（results/real_pilot.md）。
取り込み時間は data/real_pilot/timing_log.csv（取り込み作業中に記録した UNIX 時刻）から集計する。
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from .claims import gold_claims, probe_claims, write_jsonl
from .evaluate import _pct, failure_cases, metrics, run_system
from .mutate import mutations
from .normalize import ROOT, vocab
from .snapshot import Catalog, write_catalog
from .sources import is_redistributable, registry
from .splits import all_splits, leak_check, write_splits

PILOT_DIR = ROOT / "data" / "real_pilot"
PILOT_SEED = PILOT_DIR / "seed_real_pilot.yaml"
TIMING_LOG = PILOT_DIR / "timing_log.csv"
BATCH_MAKER = {"epson": "Epson", "canon": "Canon", "panasonic": "Panasonic", "calbee": "Calbee", "glico": "Glico"}


def timing_summary(log: Path = TIMING_LOG, catalog: Catalog | None = None) -> dict:
    """timing_log.csv → 規約確認・商品探索・取り込み（取得＋属性化）の秒数と、商品あたり分"""
    rows = list(csv.DictReader(log.open(encoding="utf-8")))
    starts: dict[tuple[str, str], int] = {}
    spans: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        ev, ts, label = r["event"], int(r["ts"]), r["label"]
        kind, _, phase = ev.partition("_")
        if phase == "start":
            starts[(kind, label)] = ts
        elif phase == "end" and (kind, label) in starts:
            maker = label.split("-")[0]
            spans[maker][kind] += ts - starts.pop((kind, label))
    n_by_maker = Counter(p["brand"] for p in (catalog.products.values() if catalog else []))
    makers = {}
    for key, maker in BATCH_MAKER.items():
        sp = spans.get(key, {})
        n = n_by_maker.get(maker, 0)
        ingest = sp.get("product", 0) + sp.get("batch", 0)
        # product_* は単品取得、batch_* は複数商品の一括取得。重なり（epson の単品は batch の直前）は時刻上で分離済み
        makers[maker] = {"products": n, "discover_s": sp.get("discover", 0), "ingest_s": ingest,
                         "per_product_min": round((sp.get("discover", 0) + ingest) / 60 / n, 2) if n else None}
    terms_s = spans.get("all", {}).get("terms", 0)
    total_products = sum(m["products"] for m in makers.values())
    total_s = sum(m["discover_s"] + m["ingest_s"] for m in makers.values())
    reviewed = [s for s in registry().values() if s.source_id.startswith("C1-") and s.terms_url]
    accepted = [s for s in reviewed if s.status == "allowed_internal"]
    per_product_min = total_s / 60 / total_products if total_products else 0.0
    terms_per_maker_min = terms_s / 60 / len(reviewed) if reviewed else 0.0
    accept_rate = len(accepted) / len(reviewed) if reviewed else 0.0

    def extrapolate(n: int, per_maker: int = 6) -> dict:
        makers_needed = -(-n // per_maker)
        reviews_needed = round(makers_needed / accept_rate) if accept_rate else None
        mins = n * per_product_min + (reviews_needed or 0) * terms_per_maker_min
        return {"products": n, "makers_needed": makers_needed, "terms_reviews_needed": reviews_needed,
                "agent_minutes": round(mins, 1)}

    return {"terms_review_s": terms_s, "makers_reviewed": len(reviewed), "makers_accepted": len(accepted),
            "terms_accept_rate": round(accept_rate, 3), "terms_per_maker_min": round(terms_per_maker_min, 2),
            "per_maker": makers, "products": total_products, "ingest_total_s": total_s,
            "per_product_min": round(per_product_min, 2),
            "extrapolation": [extrapolate(30), extrapolate(100), extrapolate(300)]}


def vocab_coverage(catalog: Catalog) -> dict:
    v = vocab()
    names, unrec = 0, Counter()
    for s in catalog.snapshots.values():
        for i in (s.get("ingredients") or {}).get("items", []):
            for n in ([i] if isinstance(i, str) else [i["name"], *i.get("contains", [])]):
                names += 1
                if not v.ingredient(n).recognized:
                    unrec[n] += 1
    return {"ingredient_names": names, "recognized": names - sum(unrec.values()),
            "recognized_rate": round((names - sum(unrec.values())) / names, 3) if names else None,
            "top_unrecognized": unrec.most_common(15)}


def run(seed: int = 0, out_md: Path = ROOT / "results" / "real_pilot.md") -> dict:
    from .evaluate import default_systems

    cat = Catalog.from_seed(PILOT_SEED)
    write_catalog(cat, PILOT_DIR / "manifests", PILOT_DIR / "snapshots")
    gold, muts, probes = gold_claims(cat), mutations(cat, seed=seed), probe_claims(cat)
    for name, cs in (("gold", gold), ("mutations", muts), ("probes", probes)):
        write_jsonl(cs, PILOT_DIR / "claims" / f"{name}.jsonl")
    splits = all_splits(cat)
    leaks = {s["split"]: leak_check(s, cat, gold + muts) for s in splits}
    write_splits(splits, PILOT_DIR / "splits")
    rep = {"products": len(cat.products),
           "by_brand": dict(Counter(p["brand"] for p in cat.products.values())),
           "by_category": dict(sorted(Counter(p["category"] for p in cat.products.values()).items())),
           "with_gtin": sum(1 for p in cat.products.values() if p.get("gtin")),
           "identity_key": dict(Counter("gtin" if p.get("gtin") else "model" if p.get("model_number") else "name"
                                        for p in cat.products.values())),
           "redistributable": sum(1 for p in cat.products.values() if is_redistributable(p["source_id"])),
           "claims": {"gold": len(gold), "mutation": len(muts), "probe": len(probes)},
           "mutation_by_error_type": dict(sorted(Counter(c.label["error_type"] for c in muts).items())),
           "leak_issues": {k: len(v) for k, v in leaks.items()},
           "vocab": vocab_coverage(cat), "timing": timing_summary(catalog=cat), "systems": {}}
    claims = gold + muts
    for sys_ in default_systems(cat):
        res, times = run_system(sys_, claims)
        pres, _ = run_system(sys_, probes)
        m = metrics(claims, res, times)
        rep["systems"][sys_.name] = {
            "metrics": m, "failures": failure_cases(claims, res, limit=12),
            "probe_unknown_rate": round(sum(r.verdict == "UNKNOWN" for r in pres) / len(pres), 4) if pres else None,
            "unknown_reasons": dict(Counter(r.reason.split("(")[0] for r in res if r.verdict == "UNKNOWN").most_common(8)),
        }
    with (PILOT_DIR / "ingest_minutes.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["product_id", "brand", "minutes_agent", "method"])
        for pid, p in sorted(cat.products.items()):
            w.writerow([pid, p["brand"], rep["timing"]["per_maker"][p["brand"]]["per_product_min"],
                        "メーカー単位の（探索＋取得＋属性化）÷商品数。規約確認は含まない"])
    (ROOT / "results" / "real_pilot.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str) + "\n",
                                                      encoding="utf-8")
    out_md.write_text(render_md(rep), encoding="utf-8")
    return rep


def render_md(rep: dict) -> str:
    t = rep["timing"]
    L = ["# real_pilot — 実在メーカー仕様 30 商品（自動生成）", "",
         "> `python -m src.cli real-pilot` が生成。**合成 seed（SpecBench v0）の数字と並べない別表**。",
         "> 取り込み元は規約を暫定判断したメーカー 5 社（`allowed_internal`、再配布不可）。docs/SOURCES.md 参照。", "",
         "## 内訳", "",
         f"- 商品数 {rep['products']}（{rep['by_brand']}）",
         f"- カテゴリ: {rep['by_category']}",
         f"- 同定キー: {rep['identity_key']}（JAN/GTIN あり {rep['with_gtin']} 件。Calbee はページに JAN・型番が無く商品名で同定）",
         f"- 再配布可能な商品: {rep['redistributable']} / {rep['products']}（全て allowed_internal のため公開ベンチ不可）",
         f"- claim: {rep['claims']}、mutation 類型: {rep['mutation_by_error_type']}",
         f"- スプリット漏れ検査: {rep['leak_issues']}",
         f"- 成分語彙の認識率: {_pct(rep['vocab']['recognized_rate'])}（{rep['vocab']['recognized']}/{rep['vocab']['ingredient_names']}）。"
         f"未認識の上位: {', '.join(f'{n}×{c}' for n, c in rep['vocab']['top_unrecognized'][:10])}", "",
         "## 取り込み所要時間（実測）", "",
         "> **計測対象は Claude Code（エージェント）の作業時間**（ブラウザでの閲覧・抽出・YAML 記述の壁時計）。人手の作業時間ではない。",
         "> 一括取得したメーカーはバッチ時間を商品数で割った平均。規約確認はメーカー単位の固定費として別に数える。", "",
         "| メーカー | 商品数 | 商品探索（秒） | 取得＋属性化（秒） | 商品あたり（分） |", "|---|---|---|---|---|"]
    for mk, m in t["per_maker"].items():
        L.append(f"| {mk} | {m['products']} | {m['discover_s']} | {m['ingest_s']} | {m['per_product_min']} |")
    L += ["", f"- 規約確認: {t['makers_reviewed']} 社を確認し {t['makers_accepted']} 社を allowed_internal "
          f"（採用率 {_pct(t['terms_accept_rate'])}）、計 {t['terms_review_s']} 秒（1 社 {t['terms_per_maker_min']} 分）",
          f"- 商品あたり平均 **{t['per_product_min']} 分**（探索込み、規約確認を除く）", "",
          "### 外挿（同じ作業方式・1 社 6 商品・同じ規約採用率を仮定）", "",
          "| 商品数 | 必要メーカー数 | 規約確認が必要な社数 | エージェント作業時間（分） |", "|---|---|---|---|"]
    for e in t["extrapolation"]:
        L.append(f"| {e['products']} | {e['makers_needed']} | {e['terms_reviews_needed']} | {e['agent_minutes']} |")
    L += ["", "外挿に含まれないもの: 人による規約の最終判断（法務確認）、属性化の二重チェック、ページ構造の異なるメーカーでの探索の失敗"
          "（例: Panasonic EH-NE9N は仕様ページの構造が異なり取得を見送った）、語彙（vocab.yaml）の拡充。", "",
          "## verifier 結果（real_pilot 内の gold + mutation）", "",
          "| 方式 | BLOCK precision | FAIL recall | gold PASS 率 | gold 誤 BLOCK 率 | UNKNOWN 率（gold / 誤り） | probe UNKNOWN 率 |",
          "|---|---|---|---|---|---|---|"]
    for name, s in rep["systems"].items():
        m = s["metrics"]
        L.append(f"| {name} | {_pct(m['block_precision'])} (n={m['n_block']}) | {_pct(m['fail_recall'])} | "
                 f"{_pct(m['gold_pass_rate'])} | {_pct(m['false_block_rate_on_gold'])} | "
                 f"{_pct(m['unknown_rate_gold'])} / {_pct(m['unknown_rate_errors'])} | {_pct(s['probe_unknown_rate'])} |")
    det = rep["systems"]["deterministic_v1"]
    L += ["", "類型別 FAIL recall（決定論 v1）: " + ", ".join(
        f"{k} {_pct(v['fail_recall'])} (n={v['n']})" for k, v in det["metrics"]["by_error_type"].items() if v["n"]),
          "", f"UNKNOWN の理由（決定論 v1）: {det['unknown_reasons']}", "",
          "### 失敗事例（決定論 v1）", ""]
    for kind, title in (("misses", "誤りの見逃し"), ("false_alarms", "誤検知"), ("unknown_on_true_claims", "真の主張への UNKNOWN")):
        rows = det["failures"][kind]
        L.append(f"- {title}: {det['failures']['n_' + kind if kind != 'unknown_on_true_claims' else 'n_unknown_on_true_claims']} 件")
        for r in rows[:6]:
            L.append(f"  - `{r['claim_id']}` {r['type']}/{r['attribute']} "
                     f"{'NOT ' if r['polarity'] == 'deny' else ''}{r['value']}{' ' + r['unit'] if r['unit'] else ''} "
                     f"→ {r['verdict']}/{r['outcome']}（{r['reason']}）")
    L += ["", "## 注意", "",
          "- gold / mutation は real_pilot のスナップショットから自動生成したもので、生成器と verifier が同じ語彙・許容誤差を共有する（循環的）。",
          "  実データで新たに分かるのは主に「語彙の網羅率」「同定キーの欠落」「UNKNOWN の増え方」。",
          "- Calbee・Glico のページには「共通設備で製造」等の注意喚起が無く、パッケージのみ（may_contain_source: package_only）。"
          "verifier は現状この欠落を区別しないため、「〜不使用」主張の PASS は注意喚起を考慮していない（Phase 3 バックログ）。",
          "- 「約 550 g」等の約表記は数値として記録した。", ""]
    return "\n".join(L) + "\n"
