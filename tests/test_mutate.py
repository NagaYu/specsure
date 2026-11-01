from collections import Counter

from src.mutate import ERROR_TYPES, mutations
from src.normalize import tolerances, units


def test_same_seed_same_output(bench):
    a = [c.to_dict() for c in mutations(bench, seed=7)]
    b = [c.to_dict() for c in mutations(bench, seed=7)]
    assert a == b
    c = [x.to_dict() for x in mutations(bench, seed=8)]
    assert a != c                                    # seed が効いている


def test_covers_required_error_types(bench):
    types = Counter(c.label["error_type"] for c in mutations(bench))
    for e in ("E1", "E2", "E3", "E4", "E7", "E8"):
        assert types[e] >= 10, e
    assert "E6" not in types and set(types) <= set(ERROR_TYPES)


def test_labels_and_rationale(bench):
    muts = mutations(bench)
    assert len({c.claim_id for c in muts}) == len(muts)
    for c in muts:
        lab = c.label
        assert lab["expected"] == "FAIL" and lab["error_type"] and lab["evidence_in_snapshot"] is True
        r = lab["rationale"]
        assert r["method"] and r["gold_path"] and "gold_value" in r and "mutated_value" in r
        if r["method"].startswith(("swap_generation", "swap_variant", "swap_region")):
            src = r["source_product_id"]
            kind = bench.related_kind(c.product_id, src)
            assert kind == {"E1": "generation", "E2": "sibling_variant", "E8": "region_variant"}[lab["error_type"]]


def test_numeric_mutations_really_out_of_tolerance(bench):
    u, t = units(), tolerances()
    for c in mutations(bench):
        if c.claim_type != "numeric":
            continue
        gold = c.label["rationale"]["gold_value"]
        g = u.quantity(gold["value"], gold["unit"])
        q = u.quantity(c.value, c.unit, g.dimension)
        tol = t.tolerance_si(bench.products[c.product_id]["category"], c.attribute, g.dimension, g.si())
        assert q.dimension != g.dimension or not t.within(q.si(), g.si(), tol), c.claim_id


def test_unit_swap_present(bench):
    swaps = [c for c in mutations(bench) if c.label["rationale"]["method"] == "unit_swap"]
    assert swaps
    for c in swaps:
        assert c.value == c.label["rationale"]["gold_value"]["value"] and c.unit != c.label["rationale"]["gold_value"]["unit"]
