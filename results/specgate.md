# SpecGate — PASS

- 実行: 2026-11-01T01:08:36+0900（`python scripts/specgate.py`）
- baseline: `configs/specgate_baseline.json`（pinned 2026-11-01T00:56:23+0900, commit 0bc31cab7ec65e2a5bc9ebfa70ddea57a7dba763）
- 条件: BLOCK precision ≥ 0.9、S1 素通し ≤ 0、UNKNOWN 率の変動 ≤ ±5pt

| 検査 | 結果 | 詳細 |
|---|---|---|
| pytest（オフライン） | OK | 137 passed in 3.15s（3.7 秒） |
| スプリット漏れ検査 | OK | {'loco': 0, 'loro': 0, 'temporal': 0} |
| リプレイ challenge v0 | OK | n=60、BLOCK precision 0.913、S1 素通し 0、UNKNOWN 0.0833（baseline 0.0833）、S2 recall 0.9231、gold PASS 0.8824 |
| リプレイ challenge v1 | OK | n=120、BLOCK precision 0.9487、S1 素通し 0、UNKNOWN 0.0333（baseline 0.0333）、S2 recall 0.9167、gold PASS 0.9333 |
