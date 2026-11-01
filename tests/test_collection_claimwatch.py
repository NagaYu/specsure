"""Phase 2 Step 4: 収集記録のバリデータと ClaimWatch 証跡パック（ネットワーク不要）"""

import copy
import json

import pytest

from src.claimwatch import build_pack, render_pack_md
from src.cli import main
from src.collection import validate_file, validate_record
from src.snapshot import Catalog


def good():
    return {"record_id": "r1", "collected_at": "2026-11-05T10:00:00+09:00", "agent_name": "agent-a",
            "agent_version_displayed": "not shown", "question_id": "ink-01",
            "question_text": "ICBK80Lはどのプリンターで使えますか？", "product_ref": {"model_number": "ICBK80L"},
            "answer_text": "EP-808AW に対応しています。", "collector": "collector-01",
            "terms_check_ref": "docs/collection-protocol.md#agent-a", "extractor_version": "extract-rules/v1"}


def test_valid_record_passes():
    assert validate_record(good()) == []


@pytest.mark.parametrize("mutate, msg", [
    (lambda r: r.pop("agent_version_displayed"), "missing agent_version_displayed"),
    (lambda r: r.update(collected_at="2026-11-05 10:00"), "timezone"),
    (lambda r: r.update(collected_at="yesterday"), "ISO 8601"),
    (lambda r: r.update(question_id="zzz-99"), "question_sets"),
    (lambda r: r.update(product_ref={"sku": "1"}), "product_ref"),
    (lambda r: r.update(collector="taro@example.com"), "personal contact"),
    (lambda r: r.update(notes="call 090-1234-5678"), "personal contact"),
    (lambda r: r.update(extractor_version="v1"), "extractor_version"),
    (lambda r: r.update(terms_check_ref="ok"), "terms_check_ref"),
    (lambda r: r.update(answer_text="x" * 9000), "longer than"),
    (lambda r: r.update(raw_html="<p>"), "unknown fields"),
])
def test_invalid_records(mutate, msg):
    r = good()
    mutate(r)
    assert any(msg in e for e in validate_record(r)), validate_record(r)


def test_validate_file_and_cli(tmp_path, capsys):
    p = tmp_path / "rec.jsonl"
    bad = dict(good(), record_id="r2", question_id="nope")
    p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in (good(), good(), bad)) + "\n", encoding="utf-8")
    problems = validate_file(p)
    assert set(problems) == {"r1", "r2"} and "duplicate record_id" in problems["r1"]
    assert main(["validate-records", str(p)]) == 1
    ok = tmp_path / "ok.jsonl"
    ok.write_text(json.dumps(good(), ensure_ascii=False) + "\n", encoding="utf-8")
    assert main(["validate-records", str(ok)]) == 0


def test_claimwatch_pack_on_synthetic_catalog(bench):
    rec = dict(good(), record_id="syn-1", question_id="food-02", synthetic=True,
               terms_check_ref="docs/collection-protocol.md#synthetic",
               product_ref={"gtin": bench.products["jp-choco-milk"]["gtin"]},
               answer_text="This chocolate is milk-free. Net weight is 50 g.")
    pack = build_pack(rec, bench)
    assert pack["synthetic"] and pack["decision"] == "BLOCK"
    top = pack["findings"][0]
    assert top["outcome"] == "BLOCK" and top["claim"]["polarity"] == "deny" and top["evidence"]
    assert "SYNTHETIC" in render_pack_md(pack)
