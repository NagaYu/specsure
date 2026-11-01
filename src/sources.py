"""仕様ソースの台帳とライセンスゲート。

- configs/sources.yaml が正本。docs/SOURCES.md はここから生成する（render_sources_md）
- 取り込み時は require_ingestable() を必ず通す: status=allowed かつ証拠ロールが第一者仕様のものだけ
- レビュー・Q&A（kind=user_generated）は設計ルールとして証拠に使わない
- 商品ページ全文・PDF 全文を持ち込まないため、スナップショットに本文系フィールドや長文が無いか検査する
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .normalize import ROOT, load_config

FORBIDDEN_EVIDENCE_KINDS = {"user_generated", "review", "qa"}
# allowed_internal: 内部研究用に取り込み可だが再配布不可（公開ベンチには入れない）
INGESTABLE_STATUSES = ("allowed", "allowed_internal")
EVIDENCE_ROLES = {"first_party_spec"}
RAW_TEXT_FIELDS = {"raw_html", "html", "page_text", "full_text", "pdf_text", "body", "description_full"}
MAX_STRING_LEN = 200          # 属性化された値として妥当な長さの上限

SOURCES_MD = ROOT / "docs" / "SOURCES.md"


class SourceError(ValueError):
    pass


@dataclass(frozen=True)
class Source:
    source_id: str
    name: str
    kind: str
    evidence_role: str
    acquisition: str
    license: str
    terms_note: str
    redistribution: str
    status: str
    decided_by: str
    decided_at: str
    terms_url: str = ""
    rationale: str = ""


@functools.lru_cache(maxsize=None)
def registry() -> dict[str, Source]:
    cfg = load_config("sources.yaml")
    out = {}
    for s in cfg["sources"]:
        src = Source(**{k: str(s.get(k, "")) for k in Source.__dataclass_fields__})
        if src.source_id in out:
            raise SourceError(f"duplicate source_id {src.source_id}")
        out[src.source_id] = src
    return out


def require_ingestable(source_id: str) -> Source:
    src = registry().get(source_id)
    if src is None:
        raise SourceError(f"source {source_id!r} is not registered in configs/sources.yaml; register it first")
    if src.kind in FORBIDDEN_EVIDENCE_KINDS:
        raise SourceError(f"source {source_id!r} is {src.kind}: reviews/Q&A are never evidence")
    if src.status not in INGESTABLE_STATUSES:
        raise SourceError(f"source {source_id!r} has status {src.status!r}; only {INGESTABLE_STATUSES} may be ingested")
    if src.evidence_role not in EVIDENCE_ROLES:
        raise SourceError(f"source {source_id!r} has evidence_role {src.evidence_role!r}")
    return src


def is_redistributable(source_id: str) -> bool:
    """公開ベンチに含めてよいか（allowed かつ redistribution=yes のみ）"""
    src = registry().get(source_id)
    return bool(src) and src.status == "allowed" and src.redistribution == "yes"


def check_no_raw_text(obj: Any, path: str = "$") -> None:
    """本文（ページ全文・PDF 全文）らしきものを拒否する。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in RAW_TEXT_FIELDS:
                raise SourceError(f"{path}.{k}: raw page/PDF text must not be stored")
            check_no_raw_text(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            check_no_raw_text(v, f"{path}[{i}]")
    elif isinstance(obj, str) and len(obj) > MAX_STRING_LEN and not path.endswith("source_url"):
        raise SourceError(f"{path}: string of {len(obj)} chars looks like raw text, store attributes only")


def render_sources_md() -> str:
    rows = sorted(registry().values(), key=lambda s: (s.status != "allowed", s.source_id))
    lines = [
        "# 仕様ソースとライセンス／規約判断",
        "",
        "> このファイルは `configs/sources.yaml` から `python -m src.cli sources` で生成される。直接編集しない。",
        "> 取り込みは `status = allowed` かつ `evidence_role = first_party_spec` のソースのみ（`src/sources.py` が強制）。",
        "> 規約・ライセンスが不明確なソースは取り込まず `blocked` として残す。レビュー・Q&A は証拠に使わない。",
        "",
        "| ID | ソース | 種別 | 証拠ロール | 取得方法 | ライセンス／規約 | 再配布 | 状態 | 判断 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in rows:
        if s.source_id.startswith("C1-") and s.terms_url:
            continue                                   # メーカー別の判断は下の表
        lic = f"{s.license}。{s.terms_note}" if s.terms_note else s.license
        lines.append(
            f"| `{s.source_id}` | {s.name} | {s.kind} | {s.evidence_role} | {s.acquisition} | "
            f"{lic} | {s.redistribution} | **{s.status}** | {s.decided_by} ({s.decided_at}) |"
        )
    makers = [s for s in rows if s.source_id.startswith("C1-") and s.terms_url]
    if makers:
        lines += [
            "",
            "## メーカー公式仕様ページ（C1）の規約判断 — Phase 2 real_pilot",
            "",
            "> 判断は Claude Code による**暫定**（法務確認前）。`allowed_internal` = 事実情報（型番・数値・対応機種名・原材料名）の",
            "> 少量の手作業属性化と非公開リポジトリ内での研究利用のみ可、本文・画像は保存しない、**再配布不可**（公開ベンチに入れない）。",
            "> 規約が「著作物」ではなく「情報・データ」一般の利用を私的使用に限定するもの、データ抽出・営利目的の利用を明示的に禁じるものは `blocked`。",
            "",
            "| ID | メーカー | 規約 URL | 確認日 | 状態 | 判断根拠 |",
            "|---|---|---|---|---|---|",
        ]
        for s in sorted(makers, key=lambda x: (x.status != "allowed_internal", x.source_id)):
            lines.append(f"| `{s.source_id}` | {s.name} | {s.terms_url} | {s.decided_at} | **{s.status}** | {s.rationale} |")
    lines += [
        "",
        "## Phase 1 で実際に取り込んだソース",
        "",
        "- Phase 1: `S0-synthetic-seed`（SpecBench v0 seed、すべて架空商品）と `S1-test-fixtures`（テスト専用）のみ。",
        "- Phase 2: real_pilot（30 商品）として `allowed_internal` のメーカー 5 社（Epson / Canon / Panasonic / Calbee / Glico）の",
        "  公式仕様ページから手作業で属性化（`data/real_pilot/seed_real_pilot.yaml`、合成 seed とは別マニフェスト）。",
        "- 未審査のメーカー・小売（`C1-manufacturer-spec-pages` の総称、`C2`・`C3`）は引き続き取り込まない。",
        "",
        "## 状態の定義",
        "",
        "- **allowed**: ライセンス／規約の判断済みで取り込み可",
        "- **allowed_internal**: 内部研究用に取り込み可（暫定）。再配布不可・公開ベンチには含めない",
        "- **blocked**: 規約が不明確、または再配布不可の可能性が高いため取り込まない",
        "- **reference_only**: 参照は可だが第一者仕様ではないため証拠ソースには使わない",
        "- **prohibited**: 設計ルールとして証拠に使わない（レビュー・Q&A）",
        "",
    ]
    return "\n".join(lines)


def write_sources_md(path: Path = SOURCES_MD) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_sources_md(), encoding="utf-8")
    return path
