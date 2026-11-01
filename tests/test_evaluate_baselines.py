import json

import pytest

from src.baselines import AnthropicLLMJudge, LLMJudgeBaseline, MockLLMJudge, NaiveMatcher, NETWORK_ENV
from src.claims import Claim, gold_claims
from src.evaluate import default_systems, evaluate, metrics, run_system
from src.mutate import mutations


def test_evaluation_harness_smoke(tmp_path, mini):
    systems = default_systems(mini, MockLLMJudge())
    rep = evaluate(mini, systems, seed=0, out_dir=tmp_path)
    for f in ("eval.json", "eval.md", "GATE.md"):
        assert (tmp_path / f).exists()
    data = json.loads((tmp_path / "eval.json").read_text())
    assert set(data["systems"]) == {"deterministic_v1", "naive_matcher", "llm_judge_mock"}
    det = data["systems"]["deterministic_v1"]["auto"]["metrics"]
    assert det["block_precision"] == 1.0 and det["fail_recall"] == 1.0
    assert set(det["by_severity"]) == {"S1", "S2", "S3"} and "E6" in det["by_error_type"]
    assert det["timing"]["mean_us"] > 0
    gate = (tmp_path / "GATE.md").read_text()
    assert "naive" in gate and "結論は書かない" in gate and "llm_judge_mock" in gate


def test_gate_says_llm_not_measured_without_judge(tmp_path, mini):
    evaluate(mini, default_systems(mini), out_dir=tmp_path)
    assert "実測なし" in (tmp_path / "GATE.md").read_text()


def test_naive_matcher_has_no_normalization(mini):
    n = NaiveMatcher(mini)
    p = mini.products["fx-c65-bk"]
    exact = Claim(claim_id="a", claim_type="numeric", value=65, unit="W", attribute="output_power",
                  product_ref={"gtin": p["gtin"]})
    assert n.verify_claim(exact).verdict == "PASS"
    conv = Claim(claim_id="b", claim_type="numeric", value=4.5, unit="cm", attribute="width",
                 product_ref={"gtin": p["gtin"]})
    assert n.verify_claim(conv).verdict == "FAIL"                  # 45 mm = 4.5 cm を誤検知する
    fw = Claim(claim_id="c", claim_type="numeric", value=65, unit="W", attribute="output_power",
               product_ref={"model_number": "ｋｃ－ｐｄ６５－ｂｋ"})
    assert n.verify_claim(fw).verdict == "UNKNOWN"                 # 表記揺れで同定できない


def test_mock_llm_judge_and_metrics(mini):
    judge = MockLLMJudge(lambda c, s: "FAIL")
    base = LLMJudgeBaseline(mini, judge)
    claims = gold_claims(mini)[:5] + mutations(mini)[:5]
    res, times = run_system(base, claims)
    assert judge.calls == 10 and all(r.verdict == "FAIL" for r in res)
    m = metrics(claims, res, times)
    assert m["fail_recall"] == 1.0 and m["false_fail_rate_on_gold"] == 1.0 and m["fail_precision"] == 0.5


def test_real_llm_judge_refuses_without_opt_in(monkeypatch):
    monkeypatch.delenv(NETWORK_ENV, raising=False)
    with pytest.raises(RuntimeError, match=NETWORK_ENV):
        AnthropicLLMJudge()


def test_gate_v1_report_smoke(tmp_path, mini):
    from src.evaluate import llm_call_estimate, run_gate_v1
    claims = gold_claims(mini)[:20] + mutations(mini)[:20]
    sets = {"v0": (mini, claims[:20]), "v1": (mini, claims)}
    est = llm_call_estimate(sets, "claude-opus-5-5")
    assert est["calls"] == 60 and est["est_cost_usd"] > 0          # 同定できる claim の数だけ呼ぶ
    rep = run_gate_v1(lambda cat: default_systems(cat, MockLLMJudge()), "mock", out_dir=tmp_path, sets=sets)
    md = (tmp_path / "gate-v1.md").read_text()
    assert set(rep["systems"]) == {"deterministic_v1", "naive_matcher", "llm_judge_mock"}
    assert "S1 素通し" in md and "Phase 3 バックログ" in md and "結論は書かない" in md
    assert rep["systems"]["deterministic_v1"]["v1"]["gold_pass_rate"] == 1.0


def test_evaluate_subset_sets(tmp_path, mini):
    rep = evaluate(mini, default_systems(mini), out_dir=tmp_path, only=["probe"])
    assert set(rep["sets"]) == {"probe"} and (tmp_path / "eval-probe.json").exists()
    assert not (tmp_path / "GATE.md").exists()
