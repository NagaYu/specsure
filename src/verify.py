"""決定論 verifier v1（モデルは使わない）。

処理順:
  1. product_ref（GTIN/JAN → 型番）で対象商品を同定。できなければ UNKNOWN
  2. 数値: 単位を SI に統一し tolerances.yaml の許容誤差で照合
  3. 互換性: リストと照合。リスト外は原則 UNKNOWN、網羅的とマークされた商品だけ不一致を FAIL
  4. 成分: リストと照合。不在に基づく結論は「網羅的 かつ 全成分を語彙で認識」（または表示義務アレルゲン欄）の時だけ
  5. バリアント・地域: バリアント軸・地域仕様と照合
集約: S1 の FAIL/UNKNOWN → BLOCK、S2 の FAIL → BLOCK、S3 の FAIL → WARN（configs/taxonomy.yaml）。
FAIL は必ずスナップショット上の証拠を伴う。証拠を示せない FAIL は UNKNOWN に落とす。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from .claims import Claim, severity_for, stricter
from .identity import Relation, ResolveStatus, compare_models
from .normalize import (UnitError, is_number, load_config, parse_quantity, parse_quantity_text,
                        precision_floor_si, text_key, tolerances, units, vocab)

VERIFIER_ID = "specsure-deterministic/v1"
DECISION_RANK = {"PASS": 0, "WARN": 1, "BLOCK": 2}


@dataclass
class ClaimResult:
    claim_id: str
    verdict: str                                  # PASS / FAIL / UNKNOWN
    reason: str
    severity: str
    product_id: str | None = None
    outcome: str = "PASS"                         # 集約ポリシー適用後（PASS / WARN / BLOCK）
    evidence: list[dict] = field(default_factory=list)
    diagnosis: dict | None = None                 # FAIL の推定類型 {error_type, basis, related_product_id?}
    identity: dict = field(default_factory=dict)
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class Verifier:
    def __init__(self, catalog):
        self.catalog = catalog
        tax = load_config("taxonomy.yaml")
        self.policy = tax["aggregation_policy"]
        self.default_error = tax["claim_type_default_error"]
        self.attribute_error = tax.get("attribute_default_error") or {}
        # カタログ全体の互換性項目（ブランドなし表記の一意解決用）: brandless キー → 完全キーの集合
        v = vocab()
        self.compat_universe: dict[str, set[str]] = {}
        for snap in catalog.snapshots.values():
            comp = snap.get("compatibility") or {}
            for item in comp.get("compatible", []) + comp.get("incompatible", []):
                self.compat_universe.setdefault(v.compat_key(item, brandless=True), set()).add(v.compat_key(item))

    # ───────────── 入口 ─────────────

    def verify(self, claims: Iterable[Claim | dict]) -> dict:
        results = [self.verify_claim(c) for c in claims]
        return {"verifier": VERIFIER_ID, **self.aggregate(results), "claims": [r.to_dict() for r in results]}

    def verify_claim(self, claim: Claim | dict) -> ClaimResult:
        c = claim if isinstance(claim, Claim) else Claim.from_dict(claim)
        sev = stricter(c.severity, severity_for(c.claim_type, c.attribute))
        ref = c.product_ref or {}
        res = self.catalog.identity.resolve(gtin=ref.get("gtin"), model=ref.get("model_number"), name=ref.get("name"))
        ident = {"status": res.status.value, "via": res.via, "candidates": res.candidates, "notes": res.notes}
        if not res.ok:
            return self._finish(ClaimResult(c.claim_id, "UNKNOWN", f"identity_{res.status.value}", sev,
                                            identity=ident))
        r = self._check(c, res.product_id)
        r.claim_id, r.severity, r.product_id, r.identity = c.claim_id, sev, res.product_id, ident
        if r.verdict == "FAIL":
            r.diagnosis = self._diagnose(c, res.product_id, r)
        elif (r.verdict == "UNKNOWN" and c.claim_type == "ingredient"
              and r.reason.startswith(("absence_unverifiable", "may_contain_only"))):
            # verdict は UNKNOWN のまま（S1 なので BLOCK で捕捉）。診断として E7 のリスクと理由を残す
            r.diagnosis = {"error_type": "E7", "basis": r.reason, "kind": "unverified_risk"}
        return self._finish(r)

    def _finish(self, r: ClaimResult) -> ClaimResult:
        if r.verdict == "FAIL" and not r.evidence:
            r.verdict, r.reason = "UNKNOWN", f"fail_without_evidence_suppressed({r.reason})"
            r.diagnosis = None
        r.outcome = self.policy[r.severity][r.verdict]
        return r

    def aggregate(self, results: list[ClaimResult]) -> dict:
        groups: dict[str, list[ClaimResult]] = {}
        for r in results:
            groups.setdefault(r.product_id or "unresolved", []).append(r)
        products = []
        for pid, rs in groups.items():
            decision = max((r.outcome for r in rs), key=DECISION_RANK.__getitem__, default="PASS")
            snap = self.catalog.snapshots.get(pid, {})
            products.append({
                "product_id": None if pid == "unresolved" else pid,
                "decision": decision,
                "snapshot_id": snap.get("snapshot_id"),
                "blocking_claims": [r.claim_id for r in rs if r.outcome == "BLOCK"],
                "warning_claims": [r.claim_id for r in rs if r.outcome == "WARN"],
                "unverified_claims": [r.claim_id for r in rs if r.verdict == "UNKNOWN" and r.outcome == "PASS"],
                "n_claims": len(rs),
            })
        overall = max((p["decision"] for p in products), key=DECISION_RANK.__getitem__, default="PASS")
        return {"decision": overall, "products": products}

    # ───────────── 類型別の照合 ─────────────

    def _check(self, c: Claim, pid: str) -> ClaimResult:
        fn = {
            "numeric": self._numeric, "model": self._model, "compatibility": self._compat,
            "ingredient": self._ingredient, "bundle": self._bundle, "variant": self._variant,
            "region": self._region, "price": self._price,
        }[c.claim_type]
        return fn(c, pid)

    def _ev(self, pid: str, path: str, value: Any, **extra) -> dict:
        s = self.catalog.snapshots[pid]
        return {"snapshot_id": s["snapshot_id"], "source_id": s["source_id"], "source_url": s["source_url"],
                "captured_at": s["captured_at"], "path": path, "value": value, **extra}

    @staticmethod
    def _r(verdict: str, reason: str, evidence: list | None = None, **detail) -> ClaimResult:
        return ClaimResult("", verdict, reason, "S3", evidence=evidence or [], detail=detail)

    def _compare_quantity(self, c: Claim, pid: str, path: str, gold: dict) -> ClaimResult:
        u, tol = units(), tolerances()
        category = self.catalog.products[pid]["category"]
        attr = c.attribute
        gold_unit = gold.get("unit", "")
        gdim = u.quantity(0, gold_unit).dimension
        value, unit = c.value, c.unit
        texts: list[str] = []                        # 主張値の数値表記（表記精度フロア用）
        if isinstance(value, str):
            parsed = parse_quantity_text(value)
            if parsed is None:
                return self._r("UNKNOWN", "unparseable_value")
            texts = [parsed[0]]
            value, unit = float(parsed[0].replace(",", "")), unit or parsed[1]
        try:
            if isinstance(value, dict):
                claim_q = [u.quantity(value["min"], unit, gdim), u.quantity(value["max"], unit, gdim)]
                texts = [value["min"], value["max"]]
            elif is_number(value):
                claim_q = [u.quantity(value, unit if unit is not None else "", gdim)]
                texts = texts or [value]
            else:
                return self._r("UNKNOWN", "unparseable_value")
        except UnitError as e:
            return self._r("UNKNOWN", f"unknown_unit: {e}")
        # 表記精度フロア: 主張値の最終桁の単位（換算前の単位）を SI 化した半分。既存の許容誤差と max を取る
        floor = max(precision_floor_si(t, q.unit, q.dimension) for t, q in zip(texts, claim_q))
        ev = [self._ev(pid, path, gold)]
        if claim_q[0].dimension != gdim:
            return self._r("FAIL", "unit_dimension_mismatch", ev, claim_dimension=claim_q[0].dimension,
                           gold_dimension=gdim)
        if "min" in gold:
            lo, hi = (u.quantity(gold["min"], gold_unit).si(), u.quantity(gold["max"], gold_unit).si())
            t = max(tol.tolerance_si(category, attr, gdim, hi), floor)
            if len(claim_q) == 2:
                ok = tol.within(claim_q[0].si(), lo, t) and tol.within(claim_q[1].si(), hi, t)
            else:
                ok = lo - t - 1e-12 <= claim_q[0].si() <= hi + t + 1e-12
            detail = {"claim_si": [q.si() for q in claim_q], "gold_si": [lo, hi], "tol_si": t,
                      "si_unit": u.si_unit(gdim)}
        else:
            g = u.quantity(gold["value"], gold_unit).si()
            t = max(tol.tolerance_si(category, attr, gdim, g), floor)
            ok = len(claim_q) == 1 and tol.within(claim_q[0].si(), g, t)
            detail = {"claim_si": [q.si() for q in claim_q], "gold_si": g, "tol_si": t, "si_unit": u.si_unit(gdim)}
        detail["precision_floor_si"] = floor
        ev[0]["computed"] = detail
        return self._r("PASS" if ok else "FAIL", "within_tolerance" if ok else "out_of_tolerance", ev, **detail)

    def _numeric(self, c: Claim, pid: str) -> ClaimResult:
        s = self.catalog.snapshots[pid]
        for sec in ("attributes", "region_spec"):
            gold = (s.get(sec) or {}).get(c.attribute)
            if gold is not None and ("min" in gold or is_number(gold.get("value"))):
                return self._compare_quantity(c, pid, f"{sec}.{c.attribute}", gold)
        return self._r("UNKNOWN", "attribute_not_in_snapshot")

    def _model(self, c: Claim, pid: str) -> ClaimResult:
        p = self.catalog.products[pid]
        gold = p.get("model_number")
        if not gold:
            return self._r("UNKNOWN", "no_model_number_in_catalog")
        ev = [self._ev(pid, "product.model_number", gold)]
        claimed = self.catalog.identity.resolve_model(str(c.value))
        if claimed.status is ResolveStatus.RESOLVED and claimed.product_id != pid:
            other = claimed.product_id
            ev.append(self._ev(other, "product.model_number", self.catalog.products[other]["model_number"]))
            return self._r("FAIL", "model_refers_to_other_product", ev, other_product_id=other)
        if claimed.status is ResolveStatus.RESOLVED:
            return self._r("PASS", "model_matches", ev)
        rel = compare_models(str(c.value), gold)
        if rel is Relation.SAME:
            return self._r("PASS", "model_matches_partial", ev)
        return self._r("FAIL", f"model_{rel.value}", ev)

    def _compat(self, c: Claim, pid: str) -> ClaimResult:
        comp = self.catalog.snapshots[pid].get("compatibility")
        if not comp:
            return self._r("UNKNOWN", "no_compatibility_list")
        v = vocab()
        k = v.compat_key(c.value)
        keys = {v.compat_key(i) for i in comp.get("compatible", []) + comp.get("incompatible", [])}
        resolved_from = None
        if k not in keys:
            # ブランドなし・一般語つきの表記（"Nova 5", "PX-820 printer"）は、カタログ内で一意に解決できる時だけ解決する
            bk = v.compat_key(c.value, brandless=True)
            cands = self.compat_universe.get(bk, set())
            if len(cands) == 1:
                resolved_from, k = c.value, next(iter(cands))
            elif len(cands) > 1:
                return self._r("UNKNOWN", "compat_item_ambiguous", candidates=sorted(cands))
            elif bk and not v.has_brand(c.value) and any(bk in u_ for u_ in self.compat_universe):
                return self._r("UNKNOWN", "compat_item_ambiguous_partial")
        listed = next((i for i in comp.get("compatible", []) if v.compat_key(i) == k), None)
        excluded = next((i for i in comp.get("incompatible", []) if v.compat_key(i) == k), None)
        exhaustive = bool(comp.get("exhaustive"))
        affirm = c.polarity == "affirm"
        extra = {"resolved_from": resolved_from} if resolved_from else {}
        if listed is not None:
            ev = [self._ev(pid, "compatibility.compatible", listed)]
            return self._r("PASS" if affirm else "FAIL", "listed_compatible", ev, **extra)
        if excluded is not None:
            ev = [self._ev(pid, "compatibility.incompatible", excluded)]
            return self._r("FAIL" if affirm else "PASS", "listed_incompatible", ev, **extra)
        if exhaustive:
            ev = [self._ev(pid, "compatibility.compatible", comp.get("compatible", []), exhaustive=True)]
            return self._r("FAIL" if affirm else "PASS", "not_in_exhaustive_list", ev, **extra)
        return self._r("UNKNOWN", "not_in_non_exhaustive_list", **extra)

    def _ingredient(self, c: Claim, pid: str) -> ClaimResult:
        ing = self.catalog.snapshots[pid].get("ingredients")
        if not ing:
            return self._r("UNKNOWN", "no_ingredient_list")
        v = vocab()
        ref = v.ingredient(c.value)
        names: list[tuple[str, str]] = []                 # (path, name)
        for i in ing.get("items", []):
            if isinstance(i, str):
                names.append(("ingredients.items", i))
            else:
                names.append(("ingredients.items", i["name"]))
                names += [(f"ingredients.items[{i['name']}].contains", s) for s in i.get("contains", [])]
        declared = names + [("ingredients.allergens", a) for a in ing.get("allergens", [])]

        def hits(pairs):
            out = []
            for path, n in pairs:
                r = v.ingredient(n)
                if r.key == ref.key or (ref.is_class and ref.key in r.classes):
                    out.append((path, n))
            return out

        found = hits(declared)
        maybe = hits(("ingredients.may_contain", a) for a in ing.get("may_contain", []))
        all_recognized = all(v.ingredient(n).recognized for _, n in names)
        mandatory = v.mandatory_allergens.get(self.catalog.products[pid]["region"], frozenset())
        # 不在に基づく結論には、主張側の語も語彙で認識できることが必要（「乳糖不使用」の乳糖が何に当たるか分からなければ推測しない）
        absence_ok = ref.recognized and bool(ing.get("exhaustive")) and (
            all_recognized or (ref.is_class and bool(ing.get("allergen_statement")) and ref.key in mandatory))
        affirm = c.polarity == "affirm"
        if found:
            ev = [self._ev(pid, p, n) for p, n in found]
            return self._r("PASS" if affirm else "FAIL", "declared", ev)
        if maybe:
            ev = [self._ev(pid, p, n) for p, n in maybe]
            # 「含む可能性」表示: 含有の断定はできないが、「不使用」の主張は表示と矛盾する
            return self._r("UNKNOWN", "may_contain_only", ev) if affirm else self._r("FAIL", "may_contain_conflict", ev)
        if absence_ok:
            basis = "exhaustive_list_all_recognized" if all_recognized else "mandatory_allergen_statement"
            ev = [self._ev(pid, "ingredients", {"items": ing.get("items"), "allergens": ing.get("allergens"),
                                                "exhaustive": True}, basis=basis)]
            return self._r("FAIL" if affirm else "PASS", f"absent({basis})", ev)
        why = ("claim_term_unrecognized" if not ref.recognized else
               "list_not_exhaustive" if not ing.get("exhaustive") else "unrecognized_ingredients")
        return self._r("UNKNOWN", f"absence_unverifiable({why})")

    def _bundle(self, c: Claim, pid: str) -> ClaimResult:
        b = self.catalog.snapshots[pid].get("bundle")
        if not b:
            return self._r("UNKNOWN", "no_bundle_list")
        v = vocab()
        want = v.term(c.value)
        hit = next((i for i in b["items"] if v.term(i) == want), None)
        affirm = c.polarity == "affirm"
        if hit is not None:
            return self._r("PASS" if affirm else "FAIL", "included", [self._ev(pid, "bundle.items", hit)])
        if b.get("exhaustive"):
            ev = [self._ev(pid, "bundle.items", b["items"], exhaustive=True)]
            return self._r("FAIL" if affirm else "PASS", "not_in_exhaustive_bundle", ev)
        return self._r("UNKNOWN", "not_in_non_exhaustive_bundle")

    def _match_terms(self, claimed: Any, gold: Any, category: str, attribute: str) -> bool | None:
        """バリアント値・地域の文字列値の一致。量として読めれば許容誤差で、そうでなければ語彙で比較"""
        v = vocab()
        cq, gq = parse_quantity(claimed), parse_quantity(gold)
        if cq and gq and gq[1]:
            try:
                g = units().quantity(gq[0], gq[1])
                q = units().quantity(cq[0], cq[1] or gq[1], g.dimension)
            except UnitError:
                return None
            if q.dimension != g.dimension:
                return False
            t = tolerances().tolerance_si(category, attribute, g.dimension, g.si())
            return tolerances().within(q.si(), g.si(), t)
        if v.term(claimed) == v.term(gold):
            return True
        return v.compat_key(claimed) == v.compat_key(gold)

    def _variant(self, c: Claim, pid: str) -> ClaimResult:
        p = self.catalog.products[pid]
        gold = (p.get("variant_axes") or {}).get(c.attribute)
        if gold is None:
            return self._r("UNKNOWN", "variant_axis_not_in_catalog")
        ok = self._match_terms(c.value, gold, p["category"], c.attribute)
        if ok is None:
            return self._r("UNKNOWN", "unparseable_value")
        ev = [self._ev(pid, f"variant_axes.{c.attribute}", gold)]
        return self._r("PASS" if ok else "FAIL", "variant_matches" if ok else "variant_differs", ev)

    def _region(self, c: Claim, pid: str) -> ClaimResult:
        p = self.catalog.products[pid]
        gold = (self.catalog.snapshots[pid].get("region_spec") or {}).get(c.attribute)
        if gold is None:
            return self._r("UNKNOWN", "region_attribute_not_in_snapshot")
        if "min" in gold or is_number(gold.get("value")):
            return self._compare_quantity(c, pid, f"region_spec.{c.attribute}", gold)
        ok = self._match_terms(c.value, gold["value"], p["category"], c.attribute)
        ev = [self._ev(pid, f"region_spec.{c.attribute}", gold)]
        return self._r("PASS" if ok else "FAIL", "region_matches" if ok else "region_differs", ev)

    def _price(self, c: Claim, pid: str) -> ClaimResult:
        return self._r("UNKNOWN", "price_out_of_scope_phase1(E6)")

    # ───────────── 誤りの類型推定（ClaimWatch 向けの診断） ─────────────

    def _diagnose(self, c: Claim, pid: str, r: ClaimResult) -> dict:
        cat = self.catalog
        related = ([(q, "E2") for q in cat.variant_siblings(pid)] + [(q, "E8") for q in cat.region_siblings(pid)]
                   + [(q, "E1") for q in cat.generation_neighbors(pid)])
        if c.claim_type == "model" and r.detail.get("other_product_id"):
            q = r.detail["other_product_id"]
            et = dict((x, e) for x, e in related).get(q, "E1")
            return {"error_type": et, "basis": "model_names_related_product", "related_product_id": q}
        for q, et in related:
            try:
                alt = self._check(c, q)
            except Exception:
                continue
            if alt.verdict == "PASS":
                return {"error_type": et, "basis": "claim_true_for_related_product", "related_product_id": q}
        if c.attribute in self.attribute_error:             # 入数（pack_count）→ E5 など、属性で類型が決まるもの
            return {"error_type": self.attribute_error[c.attribute], "basis": "attribute_default"}
        if c.claim_type == "numeric" and r.reason == "out_of_tolerance":
            gold = r.evidence[0]["value"] if r.evidence else {}
            if is_number(c.value) and gold.get("value") == c.value and text_key(gold.get("unit")) != text_key(c.unit):
                return {"error_type": "E3", "basis": "same_number_different_unit"}
            return {"error_type": "E3", "basis": "value_out_of_tolerance"}
        return {"error_type": self.default_error[c.claim_type], "basis": "claim_type_default"}
