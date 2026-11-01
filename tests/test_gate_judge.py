"""gate-judge（LLM judge 比較）の準備: ネットワーク不要。実 API は呼ばない（クライアントはスタブ）。"""

import hashlib
import importlib.util
import json
import types

import pytest

from src import baselines
from src.baselines import AnthropicLLMJudge, MockLLMJudge, NETWORK_ENV
from src.claims import gold_claims
from src.cli import main
from src.evaluate import RESULTS_DIR, default_systems, judge_cost_usd, llm_call_estimate, run_gate_judge
from src.mutate import mutations

# 実 judge クラスは任意依存の anthropic SDK を import する（requirements.txt では任意・コメントアウト）。
# SDK が無い環境（公開 CI など）ではこの 3 件をスキップする。API への実送信はどのテストでも行わない
needs_anthropic = pytest.mark.skipif(importlib.util.find_spec("anthropic") is None,
                                     reason="optional dependency 'anthropic' is not installed")


def _digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


@pytest.fixture()
def sets(mini):
    claims = gold_claims(mini)[:15] + mutations(mini)[:15]
    return {"v0": (mini, claims[:10]), "v1": (mini, claims)}


def test_gate_judge_mock_run_writes_separate_files_and_leaves_gate_v1(tmp_path, sets):
    before = [_digest(RESULTS_DIR / f) for f in ("gate-v1.md", "gate-v1.json")]
    judge = MockLLMJudge(lambda c, s: "FAIL", tokens=(500, 40))
    est = llm_call_estimate(sets, "claude-opus-5-5")
    rep = run_gate_judge(lambda cat, j: default_systems(cat, j), judge, est, out_dir=tmp_path, sets=sets)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["gate-judge.json", "gate-judge.md"]
    assert [_digest(RESULTS_DIR / f) for f in ("gate-v1.md", "gate-v1.json")] == before
    assert set(rep["systems"]) == {"deterministic_v1", "naive_matcher", "llm_judge_mock"}
    u = rep["judge"]["usage"]
    assert u["calls"] == est["calls"] == 40 and u["input_tokens"] == 40 * 500 and u["output_tokens"] == 40 * 40
    assert rep["judge"]["cost_usd"] == judge_cost_usd(u, "claude-opus-5-5")
    md = (tmp_path / "gate-judge.md").read_text()
    assert "MOCK — 実測ではない" in md and "結論は書かない" in md and "S1 素通し" in md
    assert rep["judge_disagreements"]                       # 常に FAIL の judge は gold と食い違う


def test_cost_formula():
    assert judge_cost_usd({"input_tokens": 1_000_000, "output_tokens": 1_000_000}, "claude-opus-5-5") == 24.0


def test_cli_refuses_without_network_flag(monkeypatch, capsys):
    monkeypatch.delenv(NETWORK_ENV, raising=False)
    before = _digest(RESULTS_DIR / "gate-judge.json")
    assert main(["gate-judge"]) == 2
    out = capsys.readouterr()
    v1_present = (RESULTS_DIR.parent / "data" / "seed" / "challenge_v1.yaml").exists()
    assert ("180 calls" if v1_present else "60 calls") in out.out and NETWORK_ENV in out.err
    assert _digest(RESULTS_DIR / "gate-judge.json") == before
    assert main(["gate-judge", "--estimate-only"]) == 0


def test_cli_max_calls_guard(monkeypatch, capsys):
    monkeypatch.setenv(NETWORK_ENV, "1")
    assert main(["gate-judge", "--max-calls", "10"]) == 2
    assert "exceeds --max-calls" in capsys.readouterr().err


@needs_anthropic
def test_real_judge_refuses_without_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv(NETWORK_ENV, "1")
    for v in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))               # ~/.config/anthropic プロファイルも無い状態
    with pytest.raises(RuntimeError, match="no Anthropic credentials"):
        AnthropicLLMJudge()


def _judge_with_stub(monkeypatch, create):
    monkeypatch.setenv(NETWORK_ENV, "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-placeholder-not-a-real-key")   # スタブ化するので送信されない
    j = AnthropicLLMJudge()
    j.client = types.SimpleNamespace(beta=types.SimpleNamespace(messages=types.SimpleNamespace(create=create)))
    return j


@needs_anthropic
def test_real_judge_records_usage_with_stubbed_client(monkeypatch):
    def create(**kw):
        assert kw["fallbacks"] == "default" and kw["output_config"]["format"]["type"] == "json_schema"
        return types.SimpleNamespace(
            stop_reason="end_turn", model="claude-opus-5-5",
            usage=types.SimpleNamespace(input_tokens=600, output_tokens=55),
            content=[types.SimpleNamespace(type="text", text=json.dumps({"verdict": "PASS", "reason": "ok"}))])
    j = _judge_with_stub(monkeypatch, create)
    assert j.judge({"claim_id": "a"}, {"snapshot_id": "s"})["verdict"] == "PASS"
    assert j.usage["calls"] == 1 and j.usage["input_tokens"] == 600 and j.usage["output_tokens"] == 55
    assert j.usage["served_models"] == {"claude-opus-5-5": 1}


@needs_anthropic
def test_real_judge_aborts_when_first_calls_all_fail(monkeypatch):
    class Boom(Exception):
        pass

    def create(**kw):
        raise Boom("401 unauthorized")
    j = _judge_with_stub(monkeypatch, create)
    j._anthropic = types.SimpleNamespace(APIError=Boom)
    for _ in range(baselines.ABORT_AFTER_INITIAL_ERRORS - 1):
        assert j.judge({}, {})["reason"].startswith("api_error")
    with pytest.raises(RuntimeError, match="aborted"):
        j.judge({}, {})
