# SpecSure

**Is the spec a shopping agent tells you actually true?** SpecSure checks product claims made by AI shopping agents — numbers with units, model and generation, variant, compatibility, bundle contents and pack counts, ingredients and allergens, regional specs — against attribute-level manufacturer spec snapshots, with a **deterministic** (model-free) verifier that never returns a FAIL without evidence.

## Headline numbers

All numbers are measured; see [BENCHMARKS.md](BENCHMARKS.md) for the full tables and how each was produced.

| | Deterministic verifier | Naive string matcher | LLM judge |
|---|---|---|---|
| BLOCK precision — unseen handwritten set (v1, 120 claims, sealed in git before its single run) | **94.7%** | 60.8% | not run |
| Safety-critical (S1) errors passed — same set | **0 / 15** | 6 / 15 | not run |
| Recall on purchase-invalidating (S2) errors — same set | 91.3% | 95.7% | not run |
| True claims passed — same set | 91.7% | 36.7% | not run |

| Claim extraction from free text (synthetic answers) | Precision | Recall |
|---|---|---|
| Extractor v1 on its hold-out (10 answers) | 92.9% | **44.8%** |
| Extractor v2 on a new hold-out (15 answers, sealed before v2 was written) | 97.6% | 91.1% |

The LLM-judge comparison is implemented (`gate-judge`) but was **not run — the author chose not to spend on API calls**; see [results/gate-judge.md](results/gate-judge.md).

## What this is — and what it is not

- **It is** a verifier that returns PASS / FAIL / UNKNOWN per claim and aggregates per product to **PASS / WARN / BLOCK** by severity (S1 safety/legal, S2 invalidates the purchase, S3 degrades the experience). Every FAIL carries the snapshot id, attribute path, value, source URL and capture time.
- **It is not** connected to any marketplace. In particular, **there is no hook that can stop Amazon's own auto-buy** (or any other native purchase flow); SpecSure only returns a verdict that an agent or a person can act on.
- **The benchmark is synthetic.** Brands, models and products in SpecBench are fictional, and the GTINs use GS1's restricted-circulation prefixes so they cannot collide with real products. A 30-product pilot built from real manufacturer pages exists, but the sites' terms permit at most internal research use, so **that data is not published** (0 / 30 redistributable); only aggregate numbers appear in BENCHMARKS.md.
- **Ingredients and allergens (E7) are handled conservatively.** Conclusions based on absence ("milk-free") require a complete ingredient list whose every term is in the vocabulary, or a mandatory-allergen statement. Otherwise the result is UNKNOWN, which **blocks** for S1. "May contain" warnings are often printed only on the package and are not in the source pages. The mandatory-allergen lists (JP 8 / US 9) are provisional and pending legal review. Do not use SpecSure as allergy advice.
- **Numbers are small-sample.** The challenge sets have 60–120 claims; one claim moves a metric by 1–2 points.

## Quick start

```bash
python -m pip install -r requirements.txt
```

```bash
python -m pytest -q
```

```bash
python scripts/specgate.py
```

```bash
uvicorn src.api:app --port 8000
```

`POST /verify` takes structured claims (`claim_type`, `attribute`, `value`, `unit`, `polarity`, and a `product_ref` with a GTIN/JAN, model number or exact product name) and returns per-claim verdicts with evidence plus a per-product decision.

## How it works

1. **Identity** — GTIN/JAN first, then a normalized model number (full-width/half-width, hyphen variants, color/capacity/region suffixes), then an exact product name.
2. **Numbers** — units converted to SI. A claim passes within the larger of a per-category tolerance and the claim's own precision (half its last digit: "6.6 ft" for a 2 m cable passes, "6.0 ft" does not).
3. **Compatibility** — matched against the official list. An unlisted item is UNKNOWN unless the list is marked exhaustive. A brand-less name is resolved only when it is unique in the catalog.
4. **Ingredients** — vocabulary-based (Japanese and English), with allergen classes. The absence rules are described above.
5. **Variants, bundles and pack counts, regional specs** — checked against the variant axes, bundle lists and region specs.
6. **Aggregation** — S1 FAIL or UNKNOWN → BLOCK; S2 FAIL → BLOCK, S2 UNKNOWN → WARN; S3 FAIL → WARN.

A rule-based extractor (`src/extract.py`) turns short agent answers into structured claims, each with the source span; precision is prioritized because a wrong extraction can cause a wrong BLOCK. `scripts/specgate.py` (also `.github/workflows/specgate.yml`) is the regression gate: offline tests, a split leak check, and a replay of the handwritten challenge sets against a pinned baseline. It fails on BLOCK precision < 0.90, any passed S1 error, or a UNKNOWN-rate drift of more than 5 points. The unseen v1 set is not in this repository, so in public CI that replay is recorded as skipped.

## Repository layout

```
src/        verifier, identity, units, extractor, mutation generator, evaluation, API, CLI
configs/    error taxonomy and severities, tolerances, units, vocabulary, source registry, SpecGate baseline
data/       synthetic seed, manifests, splits, generated claims, development challenge set
docs/       SOURCES (source and licence decisions), TAXONOMY, collection protocol (manual, terms-gated)
results/    evaluation reports
```

## Licence

All rights reserved; published for evaluation only. See [LICENSE](LICENSE). For commercial use or redistribution, please get in touch via the author's portfolio.
