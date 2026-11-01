"""ベンチ収集の記録フォーマットとバリデータ（収集そのものは手動。自動クエリ送信はしない）。

記録は 1 行 1 回答の JSONL。docs/collection-protocol.md の手順で人が収集し、このバリデータを通してから取り込む。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .normalize import load_config

REQUIRED = ("record_id", "collected_at", "agent_name", "agent_version_displayed", "question_id", "question_text",
            "product_ref", "answer_text", "collector", "terms_check_ref")
OPTIONAL = ("agent_surface", "locale", "extractor_version", "notes", "synthetic", "screenshot_ref")
MAX_ANSWER_CHARS = 8000
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\- ]{8,}\d)(?!\d)")
EXTRACTOR_RE = re.compile(r"^extract-rules/v\d+$")


def question_ids() -> dict[str, str]:
    """question_id → カテゴリ"""
    out = {}
    for cat, qs in load_config("question_sets.yaml")["categories"].items():
        for q in qs:
            out[q["id"]] = cat
    return out


def validate_record(rec: dict[str, Any]) -> list[str]:
    """問題の一覧（空なら合格）"""
    errs = []
    for k in REQUIRED:
        if rec.get(k) in (None, "", {}):
            errs.append(f"missing {k}")
    unknown = set(rec) - set(REQUIRED) - set(OPTIONAL)
    if unknown:
        errs.append(f"unknown fields {sorted(unknown)}")
    ts = rec.get("collected_at")
    if ts:
        try:
            dt = datetime.fromisoformat(str(ts))
            if dt.tzinfo is None:
                errs.append("collected_at needs a timezone offset")
        except ValueError:
            errs.append(f"collected_at is not ISO 8601: {ts!r}")
    if rec.get("question_id") and rec["question_id"] not in question_ids():
        errs.append(f"question_id {rec['question_id']!r} not in configs/question_sets.yaml")
    ref = rec.get("product_ref")
    if ref and not (isinstance(ref, dict) and set(ref) & {"gtin", "model_number", "name"}):
        errs.append("product_ref needs gtin, model_number or name")
    ans = rec.get("answer_text") or ""
    if len(ans) > MAX_ANSWER_CHARS:
        errs.append(f"answer_text longer than {MAX_ANSWER_CHARS} chars")
    for field in ("collector", "notes", "answer_text"):
        v = str(rec.get(field) or "")
        if EMAIL_RE.search(v) or PHONE_RE.search(v):
            errs.append(f"{field} looks like it contains personal contact data (email/phone)")
    if rec.get("extractor_version") and not EXTRACTOR_RE.match(rec["extractor_version"]):
        errs.append(f"extractor_version {rec['extractor_version']!r} must look like extract-rules/vN")
    if rec.get("terms_check_ref") and not str(rec["terms_check_ref"]).startswith("docs/collection-protocol.md#"):
        errs.append("terms_check_ref must point to a row in docs/collection-protocol.md (e.g. docs/collection-protocol.md#agent-a)")
    return errs


def validate_file(path: Path) -> dict[str, list[str]]:
    out, ids = {}, set()
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            out[f"line {n}"] = [f"invalid JSON: {e}"]
            continue
        errs = validate_record(rec)
        rid = rec.get("record_id", f"line {n}")
        if rid in ids:
            errs.append("duplicate record_id")
        ids.add(rid)
        if errs:
            out[str(rid)] = errs
    return out
