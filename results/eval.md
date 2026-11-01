# SpecBench v0 — verifier 評価結果

seed=0 / 商品数 51 / 生成: `python -m src.cli evaluate`

| 評価セット | claim 数 | 正解内訳 |
|---|---|---|
| auto | 958 | {'PASS': 545, 'FAIL': 413} |
| challenge | 60 | {'PASS': 34, 'FAIL': 26} |
| probe | 57 | {'UNKNOWN': 57} |

## deterministic_v1

### auto

| 指標 | 値 |
|---|---|
| BLOCK precision | 100.0% (BLOCK 235 件) |
| FAIL precision | 100.0% |
| FAIL recall（誤り claim） | 100.0% |
| 検出 recall（BLOCK/WARN） | 100.0% |
| gold の誤 FAIL 率 | 0.0% |
| gold の誤 BLOCK 率 | 0.0% |
| UNKNOWN 率（全体 / gold / 誤り） | 0.0% / 0.0% / 0.0% |
| 同定成功率 | 100.0% |
| S1 の誤りを PASS した件数 | 0 |
| 処理時間 | 合計 43.27 ms / 平均 45.2 µs / p95 131.5 µs |

| 重大度 | n | FAIL recall | BLOCK/WARN recall | BLOCK recall |
|---|---|---|---|---|
| S1 | 86 | 100.0% | 100.0% | 100.0% |
| S2 | 149 | 100.0% | 100.0% | 100.0% |
| S3 | 178 | 100.0% | 100.0% | 0.0% |

| 類型 | n | FAIL recall | BLOCK/WARN recall | UNKNOWN | 診断一致率 |
|---|---|---|---|---|---|
| E1 | 34 | 100.0% | 100.0% | 0 | 97.1% |
| E2 | 64 | 100.0% | 100.0% | 0 | 96.9% |
| E3 | 102 | 100.0% | 100.0% | 0 | 93.1% |
| E4 | 54 | 100.0% | 100.0% | 0 | 48.1% |
| E5 | 42 | 100.0% | 100.0% | 0 | 97.6% |
| E6 | — | 対象外（Phase 1） | — | — | — |
| E7 | 42 | 100.0% | 100.0% | 0 | 71.4% |
| E8 | 75 | 100.0% | 100.0% | 0 | 80.0% |

### challenge

| 指標 | 値 |
|---|---|
| BLOCK precision | 91.3% (BLOCK 23 件) |
| FAIL precision | 92.0% |
| FAIL recall（誤り claim） | 88.5% |
| 検出 recall（BLOCK/WARN） | 100.0% |
| gold の誤 FAIL 率 | 5.9% |
| gold の誤 BLOCK 率 | 5.9% |
| UNKNOWN 率（全体 / gold / 誤り） | 8.3% / 5.9% / 11.5% |
| 同定成功率 | 100.0% |
| S1 の誤りを PASS した件数 | 0 |
| 処理時間 | 合計 2.87 ms / 平均 47.8 µs / p95 131.3 µs |

| 重大度 | n | FAIL recall | BLOCK/WARN recall | BLOCK recall |
|---|---|---|---|---|
| S1 | 9 | 77.8% | 100.0% | 100.0% |
| S2 | 13 | 92.3% | 100.0% | 92.3% |
| S3 | 4 | 100.0% | 100.0% | 0.0% |

| 類型 | n | FAIL recall | BLOCK/WARN recall | UNKNOWN | 診断一致率 |
|---|---|---|---|---|---|
| E1 | 5 | 100.0% | 100.0% | 0 | 100.0% |
| E2 | 5 | 100.0% | 100.0% | 0 | 100.0% |
| E3 | 2 | 100.0% | 100.0% | 0 | 100.0% |
| E4 | 1 | 100.0% | 100.0% | 0 | 0.0% |
| E5 | 1 | 100.0% | 100.0% | 0 | 100.0% |
| E6 | — | 対象外（Phase 1） | — | — | — |
| E7 | 7 | 71.4% | 100.0% | 2 | 57.1% |
| E8 | 5 | 80.0% | 100.0% | 1 | 100.0% |

### probe（正解 UNKNOWN）

UNKNOWN 率 100.0%（57 件）、S1 プローブの BLOCK 率 100.0%（4 件）

| 種別 | 判定内訳 |
|---|---|
| ambiguous-model | {'UNKNOWN': 8} |
| compat-unlisted | {'UNKNOWN': 7} |
| ingredient-contains-unverifiable | {'UNKNOWN': 2} |
| ingredient-free-unverifiable | {'UNKNOWN': 2} |
| missing-attr | {'UNKNOWN': 17} |
| price | {'UNKNOWN': 19} |
| unknown-gtin | {'UNKNOWN': 1} |
| unknown-model | {'UNKNOWN': 1} |

## naive_matcher

### auto

| 指標 | 値 |
|---|---|
| BLOCK precision | 52.7% (BLOCK 258 件) |
| FAIL precision | 64.1% |
| FAIL recall（誤り claim） | 32.9% |
| 検出 recall（BLOCK/WARN） | 68.0% |
| gold の誤 FAIL 率 | 13.9% |
| gold の誤 BLOCK 率 | 22.4% |
| UNKNOWN 率（全体 / gold / 誤り） | 62.6% / 62.0% / 63.4% |
| 同定成功率 | 37.4% |
| S1 の誤りを PASS した件数 | 4 |
| 処理時間 | 合計 1.16 ms / 平均 1.2 µs / p95 1.5 µs |

| 重大度 | n | FAIL recall | BLOCK/WARN recall | BLOCK recall |
|---|---|---|---|---|
| S1 | 86 | 29.1% | 95.3% | 95.3% |
| S2 | 149 | 36.2% | 95.3% | 36.2% |
| S3 | 178 | 32.0% | 32.0% | 0.0% |

| 類型 | n | FAIL recall | BLOCK/WARN recall | UNKNOWN | 診断一致率 |
|---|---|---|---|---|---|
| E1 | 34 | 38.2% | 70.6% | 21 | — |
| E2 | 64 | 26.6% | 85.9% | 46 | — |
| E3 | 102 | 34.3% | 43.1% | 67 | — |
| E4 | 54 | 29.6% | 87.0% | 31 | — |
| E5 | 42 | 28.6% | 45.2% | 26 | — |
| E6 | — | 対象外（Phase 1） | — | — | — |
| E7 | 42 | 26.2% | 95.2% | 29 | — |
| E8 | 75 | 42.7% | 69.3% | 42 | — |

### challenge

| 指標 | 値 |
|---|---|
| BLOCK precision | 42.4% (BLOCK 33 件) |
| FAIL precision | 41.9% |
| FAIL recall（誤り claim） | 69.2% |
| 検出 recall（BLOCK/WARN） | 73.1% |
| gold の誤 FAIL 率 | 73.5% |
| gold の誤 BLOCK 率 | 55.9% |
| UNKNOWN 率（全体 / gold / 誤り） | 1.7% / 0.0% / 3.9% |
| 同定成功率 | 100.0% |
| S1 の誤りを PASS した件数 | 7 |
| 処理時間 | 合計 0.09 ms / 平均 1.5 µs / p95 1.8 µs |

| 重大度 | n | FAIL recall | BLOCK/WARN recall | BLOCK recall |
|---|---|---|---|---|
| S1 | 9 | 22.2% | 22.2% | 22.2% |
| S2 | 13 | 92.3% | 100.0% | 92.3% |
| S3 | 4 | 100.0% | 100.0% | 0.0% |

| 類型 | n | FAIL recall | BLOCK/WARN recall | UNKNOWN | 診断一致率 |
|---|---|---|---|---|---|
| E1 | 5 | 100.0% | 100.0% | 0 | — |
| E2 | 5 | 100.0% | 100.0% | 0 | — |
| E3 | 2 | 100.0% | 100.0% | 0 | — |
| E4 | 1 | 100.0% | 100.0% | 0 | — |
| E5 | 1 | 100.0% | 100.0% | 0 | — |
| E6 | — | 対象外（Phase 1） | — | — | — |
| E7 | 7 | 14.3% | 14.3% | 0 | — |
| E8 | 5 | 60.0% | 80.0% | 1 | — |

### probe（正解 UNKNOWN）

UNKNOWN 率 80.7%（57 件）、S1 プローブの BLOCK 率 50.0%（4 件）

| 種別 | 判定内訳 |
|---|---|
| ambiguous-model | {'UNKNOWN': 8} |
| compat-unlisted | {'FAIL': 7} |
| ingredient-contains-unverifiable | {'FAIL': 2} |
| ingredient-free-unverifiable | {'PASS': 2} |
| missing-attr | {'UNKNOWN': 17} |
| price | {'UNKNOWN': 19} |
| unknown-gtin | {'UNKNOWN': 1} |
| unknown-model | {'UNKNOWN': 1} |
