"""ClaimWatch 証跡パック: 収集記録（質問・回答）→ 抽出 → 照合 → 仕様証拠つきのレポート。

Phase 2 では合成のエージェント風回答による見本 1 件だけを生成する（SYNTHETIC と明記）。
実在エージェントの回答は、ユーザーから渡され、docs/collection-protocol.md の規約確認が済んだものだけ処理する。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .collection import validate_record
from .extract import EXTRACTOR_VERSION, extract
from .normalize import ROOT
from .verify import VERIFIER_ID, Verifier

SYNTHETIC_BANNER = ("SYNTHETIC — この回答は Claude Code が作成した合成文で、実在の買い物エージェントの回答ではない。"
                    "商品仕様の証拠はメーカー公式ページの属性化スナップショット（real_pilot、内部研究用・再配布不可）。")

# 見本の収集記録（合成回答 × real_pilot の商品）は実データ側に置く（再配布不可のため公開ツリーには含めない）
SAMPLE_RECORD_PATH = ROOT / "data" / "real_pilot" / "claimwatch_sample_record.json"


def sample_record() -> dict:
    return json.loads(SAMPLE_RECORD_PATH.read_text(encoding="utf-8"))


def build_pack(record: dict, catalog) -> dict:
    errs = validate_record(record)
    if errs:
        raise ValueError(f"record does not pass the collection validator: {errs}")
    if not record.get("synthetic") and not record.get("terms_check_ref", "").startswith("docs/collection-protocol.md#agent-"):
        raise ValueError("real agent answers need a completed terms check row")
    res = extract(record["answer_text"], record["product_ref"], id_prefix=record["record_id"], catalog=catalog)
    report = Verifier(catalog).verify([e.claim for e in res.claims])
    by_id = {c["claim_id"]: c for c in report["claims"]}
    pid = next((p["product_id"] for p in report["products"] if p["product_id"]), None)
    product = catalog.products.get(pid, {})
    findings = []
    for e in res.claims:
        r = by_id[e.claim.claim_id]
        findings.append({
            "answer_span": {"start": e.span.start, "end": e.span.end, "text": e.span.text},
            "claim": {k: e.claim.to_dict()[k] for k in ("claim_type", "attribute", "value", "unit", "polarity")},
            "verdict": r["verdict"], "severity": r["severity"], "outcome": r["outcome"], "reason": r["reason"],
            "diagnosis": r["diagnosis"],
            "evidence": [{k: ev.get(k) for k in ("snapshot_id", "path", "value", "source_url", "captured_at")}
                         for ev in r["evidence"]],
        })
    order = {"BLOCK": 0, "WARN": 1, "PASS": 2}
    findings.sort(key=lambda f: order[f["outcome"]])
    return {
        "pack_id": f"cw-{record['record_id']}",
        "synthetic": bool(record.get("synthetic")),
        "banner": SYNTHETIC_BANNER if record.get("synthetic") else None,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "record": {k: record.get(k) for k in ("record_id", "collected_at", "agent_name", "agent_version_displayed",
                                               "question_id", "question_text", "answer_text", "locale")},
        "product": {k: product.get(k) for k in ("product_id", "product_name", "brand", "gtin", "model_number",
                                                 "source_url", "captured_at", "snapshot_id")},
        "decision": report["decision"],
        "coverage": {"sentences": len(res.sentences), "covered": sum(res.covered)},
        "findings": findings,
        "versions": {"extractor": EXTRACTOR_VERSION, "verifier": VERIFIER_ID},
        "disclaimer": ("判定は公式ページのスナップショット（取得日時つき）との照合結果であり、パッケージ表示・最新仕様と異なる場合がある。"
                       "「共通設備で製造」等の注意喚起はこのソースに含まれない。"),
    }


def render_pack_md(pack: dict) -> str:
    L = [f"# ClaimWatch 証跡パック `{pack['pack_id']}`", ""]
    if pack["synthetic"]:
        L += [f"> **{pack['banner']}**", ""]
    r, p = pack["record"], pack["product"]
    L += ["| 項目 | 内容 |", "|---|---|",
          f"| 収集日時 | {r['collected_at']} |",
          f"| エージェント | {r['agent_name']}（表示バージョン: {r['agent_version_displayed']}） |",
          f"| 質問（{r['question_id']}） | {r['question_text']} |",
          f"| 対象商品 | {p['product_name'] or p['model_number']}（{p['brand']}、`{p['product_id']}`） |",
          f"| 仕様の出典 | {p['source_url']}（取得 {p['captured_at']}、`{p['snapshot_id']}`） |",
          f"| **総合判定** | **{pack['decision']}** |",
          f"| 生成 | {pack['generated_at']}、{pack['versions']['extractor']} / {pack['versions']['verifier']} |", "",
          "## 回答原文", "", f"> {r['answer_text']}", "",
          "## 主張ごとの判定", "",
          "| 回答の該当箇所 | 主張 | 判定 | 重大度 | 結果 | 仕様の証拠 |", "|---|---|---|---|---|---|"]
    for f in pack["findings"]:
        c = f["claim"]
        claim = f"{c['claim_type']}: {'NOT ' if c['polarity'] == 'deny' else ''}{c['value']}{' ' + c['unit'] if c['unit'] else ''}"
        ev = "; ".join(f"`{e['path']}` = {json.dumps(e['value'], ensure_ascii=False)}" for e in f["evidence"]) or "—"
        L.append(f"| 「{f['answer_span']['text']}」 | {claim} | {f['verdict']} | {f['severity']} | **{f['outcome']}** | {ev} |")
    L += ["", f"抽出: {pack['coverage']['covered']}/{pack['coverage']['sentences']} 文から主張を抽出。", "",
          f"_{pack['disclaimer']}_", ""]
    return "\n".join(L)


def write_sample(out_dir: Path = ROOT / "results" / "claimwatch") -> Path:
    from .real_pilot import PILOT_SEED
    from .snapshot import Catalog

    pack = build_pack(sample_record(), Catalog.from_seed(PILOT_SEED))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sample_pack_001.json").write_text(json.dumps(pack, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    md = out_dir / "sample_pack_001.md"
    md.write_text(render_pack_md(pack), encoding="utf-8")
    return md
