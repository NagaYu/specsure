"""SpecGate の判定ロジック（fail 条件）"""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("specgate", Path(__file__).resolve().parent.parent / "scripts" / "specgate.py")
specgate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(specgate)

BASE = {"replay": {"v0": {"unknown_rate": 0.08}, "v1": {"unknown_rate": 0.03}}}


def row(bp=0.95, s1=0, unk=0.08):
    return {"block_precision": bp, "s1_leak": s1, "s1_leak_ids": ["x"] * s1, "unknown_rate": unk}


def test_pass_within_thresholds():
    assert specgate.judge({"v0": row(), "v1": row(unk=0.06)}, BASE) == []     # v1: +3pt は許容


def test_fail_conditions():
    assert any("BLOCK precision" in f for f in specgate.judge({"v0": row(bp=0.89)}, BASE))
    assert any("S1 leak" in f for f in specgate.judge({"v0": row(s1=1)}, BASE))
    assert any("drifted" in f for f in specgate.judge({"v0": row(unk=0.14)}, BASE))
    assert any("drifted" in f for f in specgate.judge({"v0": row(unk=0.02)}, BASE))
    assert any("no pinned baseline" in f for f in specgate.judge({"v2": row()}, BASE))


def test_missing_holdout_is_skipped_not_failed():
    assert specgate.judge({"v0": row(), "v1": {"skipped": "holdout not present in this tree"}}, BASE) == []
