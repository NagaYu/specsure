"""ベースライン判定器。

(a) NaiveMatcher: 正規化なしの素朴な一致判定（文字列の完全一致・数値と単位文字列の完全一致）
(b) LLM judge: interface（LLMJudge）とモック、実キーで手動実行する AnthropicLLMJudge。
    実 API 呼び出しは --llm-judge フラグ かつ 環境変数 SPECSURE_ALLOW_NETWORK=1 の時だけ。
    テスト・通常実行ではネットワークを叩かない。

どちらも決定論 verifier と同じ重大度・集約ポリシーで outcome を付け、同じ指標で比較できるようにする。
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Protocol

from .claims import Claim, severity_for, stricter
from .normalize import is_number, load_config
from .verify import ClaimResult

NETWORK_ENV = "SPECSURE_ALLOW_NETWORK"
DEFAULT_LLM_MODEL = "claude-opus-5-5"
ABORT_AFTER_INITIAL_ERRORS = 3


def _policy() -> dict:
    return load_config("taxonomy.yaml")["aggregation_policy"]


def _finish(c: Claim, verdict: str, reason: str, pid: str | None, evidence: list | None = None) -> ClaimResult:
    sev = stricter(c.severity, severity_for(c.claim_type, c.attribute))
    return ClaimResult(c.claim_id, verdict, reason, sev, product_id=pid, outcome=_policy()[sev][verdict],
                       evidence=evidence or [])


# ───────────────────────────── (a) naive matcher ─────────────────────────────

class NaiveMatcher:
    """GTIN/型番はカタログ表記と文字列完全一致、値は gold と文字列完全一致なら PASS、それ以外は FAIL。
    リストの網羅性・単位換算・表記揺れ・アレルゲンクラスは考慮しない。"""

    name = "naive_matcher"

    def __init__(self, catalog):
        self.catalog = catalog
        self.by_gtin = {p["gtin"]: pid for pid, p in catalog.products.items() if p.get("gtin")}
        self.by_model = {p["model_number"]: pid for pid, p in catalog.products.items() if p.get("model_number")}
        self.by_name = {p["product_name"]: pid for pid, p in catalog.products.items() if p.get("product_name")}

    def _identify(self, ref: dict) -> str | None:
        return (self.by_gtin.get(str(ref.get("gtin"))) or self.by_model.get(str(ref.get("model_number")))
                or self.by_name.get(str(ref.get("name"))))

    def verify_claim(self, c: Claim) -> ClaimResult:
        pid = self._identify(c.product_ref or {})
        if pid is None:
            return _finish(c, "UNKNOWN", "identity_exact_match_failed", None)
        p, s = self.catalog.products[pid], self.catalog.snapshots[pid]
        t, v = c.claim_type, c.value
        affirm = c.polarity == "affirm"

        def eq(a: Any, b: Any) -> bool:
            return str(a) == str(b)

        if t in ("numeric", "region"):
            gold = (s.get("attributes") or {}).get(c.attribute) or (s.get("region_spec") or {}).get(c.attribute)
            if gold is None:
                return _finish(c, "UNKNOWN", "attribute_missing", pid)
            if "min" in gold:
                ok = (isinstance(v, dict) and eq(v.get("min"), gold["min"]) and eq(v.get("max"), gold["max"])
                      and eq(c.unit, gold.get("unit")))
            elif is_number(gold.get("value")):
                ok = is_number(v) and float(v) == float(gold["value"]) and eq(c.unit, gold.get("unit"))
            else:
                ok = eq(v, gold["value"])
        elif t == "model":
            ok = eq(v, p.get("model_number"))
        elif t == "variant":
            gold = (p.get("variant_axes") or {}).get(c.attribute)
            if gold is None:
                return _finish(c, "UNKNOWN", "attribute_missing", pid)
            ok = eq(v, gold)
        elif t == "compatibility":
            comp = s.get("compatibility")
            if not comp:
                return _finish(c, "UNKNOWN", "no_list", pid)
            ok = (v in comp.get("compatible", [])) == affirm
        elif t == "ingredient":
            ing = s.get("ingredients")
            if not ing:
                return _finish(c, "UNKNOWN", "no_list", pid)
            names = [i if isinstance(i, str) else i["name"] for i in ing["items"]] + list(ing.get("allergens", []))
            ok = (v in names) == affirm
        elif t == "bundle":
            b = s.get("bundle")
            if not b:
                return _finish(c, "UNKNOWN", "no_list", pid)
            ok = (v in b["items"]) == affirm
        else:
            return _finish(c, "UNKNOWN", "unsupported", pid)
        return _finish(c, "PASS" if ok else "FAIL", "exact_match" if ok else "mismatch", pid,
                       [{"snapshot_id": s["snapshot_id"], "path": t}])


# ───────────────────────────── (b) LLM judge ─────────────────────────────

class LLMJudge(Protocol):
    name: str

    def judge(self, claim: dict, snapshot: dict) -> dict:
        """{"verdict": "PASS"|"FAIL"|"UNKNOWN", "reason": str}"""


class MockLLMJudge:
    """テスト用。fn(claim_view, snapshot) → verdict。既定は常に UNKNOWN。ネットワークを使わない。"""

    name = "llm_judge_mock"
    is_mock = True
    model = "mock"
    effort = "n/a"

    def __init__(self, fn: Callable[[dict, dict], str] | None = None, tokens: tuple[int, int] = (0, 0)):
        self.fn = fn or (lambda c, s: "UNKNOWN")
        self.calls = 0
        self.usage = new_usage()
        self._tokens = tokens

    def judge(self, claim: dict, snapshot: dict) -> dict:
        self.calls += 1
        self.usage["calls"] += 1
        self.usage["input_tokens"] += self._tokens[0]
        self.usage["output_tokens"] += self._tokens[1]
        self.usage["served_models"]["mock"] = self.usage["served_models"].get("mock", 0) + 1
        return {"verdict": self.fn(claim, snapshot), "reason": "mock"}


def new_usage() -> dict:
    """judge の実使用量（gate-judge.md に記録する）"""
    return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "errors": 0, "refusals": 0, "unparseable": 0,
            "served_models": {}, "seconds": 0.0}


JUDGE_SYSTEM = (
    "You verify a single product claim made by a shopping agent against the manufacturer specification snapshot "
    "provided. Use only the snapshot. Answer PASS if the snapshot supports the claim, FAIL if the snapshot "
    "contradicts it, UNKNOWN if the snapshot does not let you decide. Lists marked exhaustive=false are partial. "
    "Do not use outside knowledge; reviews and Q&A are not evidence."
)
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["PASS", "FAIL", "UNKNOWN"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "reason"],
    "additionalProperties": False,
}


class AnthropicLLMJudge:
    """実キーで手動実行する LLM judge（Claude API, 公式 Python SDK）。

    前提: `pip install anthropic`、ANTHROPIC_API_KEY（または `ant auth login` のプロファイル）、
          SPECSURE_ALLOW_NETWORK=1。いずれも満たさない場合は構築時に例外を出し、勝手に送信しない。
    拒否（stop_reason=refusal）時はサーバ側フォールバック（fallbacks="default"）に任せ、最終的に拒否なら UNKNOWN。
    """

    name = "llm_judge_claude"
    is_mock = False

    def __init__(self, model: str | None = None, effort: str = "low"):
        if os.environ.get(NETWORK_ENV) != "1":
            raise RuntimeError(f"LLM judge needs {NETWORK_ENV}=1 (explicit opt-in to network calls)")
        try:
            import anthropic
        except ImportError as e:
            raise RuntimeError("pip install anthropic to run the LLM judge baseline") from e
        self.client = anthropic.Anthropic()
        has_profile = os.path.isdir(os.path.expanduser("~/.config/anthropic"))
        if not (self.client.api_key or getattr(self.client, "auth_token", None) or has_profile):
            # SDK は認証情報なしでも構築できてしまう。全呼び出しが失敗した結果を実測として残さないよう、ここで止める
            raise RuntimeError("no Anthropic credentials (set ANTHROPIC_API_KEY in the environment; never in the repo)")
        self.model = model or os.environ.get("SPECSURE_LLM_MODEL", DEFAULT_LLM_MODEL)
        self.effort = effort
        self.usage = new_usage()
        self._anthropic = anthropic

    def judge(self, claim: dict, snapshot: dict) -> dict:
        import time

        prompt = ("Snapshot:\n" + json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
                  + "\n\nClaim:\n" + json.dumps(claim, ensure_ascii=False, sort_keys=True))
        u = self.usage
        u["calls"] += 1
        t0 = time.perf_counter()
        try:
            resp = self.client.beta.messages.create(
                model=self.model,
                max_tokens=2048,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=JUDGE_SYSTEM,
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
                messages=[{"role": "user", "content": prompt}],
            )
        except self._anthropic.APIError as e:            # SDK の自動リトライ後も失敗した呼び出しは UNKNOWN として数える
            u["errors"] += 1
            u["seconds"] += time.perf_counter() - t0
            if u["errors"] == u["calls"] and u["calls"] >= ABORT_AFTER_INITIAL_ERRORS:
                # 最初から連続で失敗（認証・モデル名・権限など）。結果ファイルを書く前に中断する
                raise RuntimeError(f"LLM judge aborted: first {u['calls']} calls all failed ({type(e).__name__}: {e})") from e
            return {"verdict": "UNKNOWN", "reason": f"api_error:{type(e).__name__}"}
        u["seconds"] += time.perf_counter() - t0
        u["input_tokens"] += getattr(resp.usage, "input_tokens", 0) or 0
        u["output_tokens"] += getattr(resp.usage, "output_tokens", 0) or 0
        served = getattr(resp, "model", self.model) or self.model
        u["served_models"][served] = u["served_models"].get(served, 0) + 1
        if resp.stop_reason == "refusal":
            u["refusals"] += 1
            return {"verdict": "UNKNOWN", "reason": "refusal"}
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            out = json.loads(text)
        except json.JSONDecodeError:
            u["unparseable"] += 1
            return {"verdict": "UNKNOWN", "reason": "unparseable_judge_output"}
        return {"verdict": out.get("verdict", "UNKNOWN"), "reason": out.get("reason", "")}


class LLMJudgeBaseline:
    """同定は決定論 verifier と同じ identity 解決を使い、照合だけを LLM に任せる（判定部分の比較にするため）。"""

    def __init__(self, catalog, judge: LLMJudge):
        self.catalog, self.judge = catalog, judge
        self.name = judge.name

    def verify_claim(self, c: Claim) -> ClaimResult:
        ref = c.product_ref or {}
        res = self.catalog.identity.resolve(gtin=ref.get("gtin"), model=ref.get("model_number"), name=ref.get("name"))
        if not res.ok:
            return _finish(c, "UNKNOWN", f"identity_{res.status.value}", None)
        snap = self.catalog.snapshots[res.product_id]
        product = {k: self.catalog.products[res.product_id].get(k)
                   for k in ("model_number", "brand", "category", "region", "variant_axes")}
        out = self.judge.judge(c.verifier_view(), {"product": product, **snap})
        verdict = out.get("verdict") if out.get("verdict") in ("PASS", "FAIL", "UNKNOWN") else "UNKNOWN"
        return _finish(c, verdict, f"llm:{out.get('reason', '')}"[:200], res.product_id,
                       [{"snapshot_id": snap["snapshot_id"], "path": "llm_judgement"}])
