# Week 2 ゲート判定材料（自動生成）

> このファイルは `python -m src.cli evaluate` が生成する。**結論は書かない**。Go/No-Go の判断は Yuta が行う。
> 数字は SpecBench v0（51 商品・全て合成データ、mutation seed=0）上のもので、実エージェント回答・実在商品での性能を意味しない。

## 1. 決定論 verifier と naive matcher の差

### auto（gold + mutation、生成器と verifier が定義を共有＝上限寄り）

| 指標 | 決定論 v1 | naive | 差（v1 − naive） |
|---|---|---|---|
| BLOCK precision | 100.0% | 52.7% | +47.3 pt |
| FAIL precision | 100.0% | 64.1% | +35.9 pt |
| FAIL recall（全誤り） | 100.0% | 32.9% | +67.1 pt |
| gold の誤 BLOCK 率 | 0.0% | 22.4% | -22.4 pt |
| UNKNOWN 率 | 0.0% | 62.6% | -62.6 pt |
| 同定成功率 | 100.0% | 37.4% | +62.6 pt |
| FAIL precision（同定できた claim のみ） | 100.0% | 64.1% | +35.9 pt |
| FAIL recall（同定できた claim のみ） | 100.0% | 90.1% | +9.9 pt |
| S1 recall（FAIL, n=86） | 100.0% | 29.1% | +70.9 pt |
| S2 recall（FAIL, n=149） | 100.0% | 36.2% | +63.8 pt |
| S3 recall（FAIL, n=178） | 100.0% | 32.0% | +68.0 pt |
| S1 BLOCK recall（UNKNOWN→BLOCK 含む） | 100.0% | 95.3% | +4.6 pt |
| S1 の誤りを PASS した件数 | 0 | 4 | |
| 平均処理時間 / claim | 45.2 µs | 1.2 µs | |

### challenge（手書き、語彙外の言い回しと現実的な誤り）

| 指標 | 決定論 v1 | naive | 差（v1 − naive） |
|---|---|---|---|
| BLOCK precision | 91.3% | 42.4% | +48.9 pt |
| FAIL precision | 92.0% | 41.9% | +50.1 pt |
| FAIL recall（全誤り） | 88.5% | 69.2% | +19.2 pt |
| gold の誤 BLOCK 率 | 5.9% | 55.9% | -50.0 pt |
| UNKNOWN 率 | 8.3% | 1.7% | +6.7 pt |
| 同定成功率 | 100.0% | 100.0% | +0.0 pt |
| FAIL precision（同定できた claim のみ） | 92.0% | 41.9% | +50.1 pt |
| FAIL recall（同定できた claim のみ） | 88.5% | 69.2% | +19.2 pt |
| S1 recall（FAIL, n=9） | 77.8% | 22.2% | +55.6 pt |
| S2 recall（FAIL, n=13） | 92.3% | 92.3% | +0.0 pt |
| S3 recall（FAIL, n=4） | 100.0% | 100.0% | +0.0 pt |
| S1 BLOCK recall（UNKNOWN→BLOCK 含む） | 100.0% | 22.2% | +77.8 pt |
| S1 の誤りを PASS した件数 | 0 | 7 | |
| 平均処理時間 / claim | 47.8 µs | 1.5 µs | |

### probe（スナップショットから判断できない主張、正解 UNKNOWN）

- 決定論 v1: UNKNOWN 率 100.0%、S1 プローブの BLOCK 率 100.0%
- naive: UNKNOWN 率 80.7%、S1 プローブの BLOCK 率 50.0%

## 2. LLM judge

**実測なし。** LLM judge は interface とモックのみ実装済み。実キーでの実行手順は README「LLM judge を実キーで回す」を参照。

## 3. 失敗事例（決定論 v1）

### challenge: 誤りの見逃し（正解 FAIL → FAIL 以外）3 件

| claim | 商品 | 種別 | 値 | 重大度 | 正解 | 判定 | 理由 | メモ |
|---|---|---|---|---|---|---|---|---|
| `x-c53` | jp-sunscreen-80g | ingredient/contains | NOT alcohol | S1 | FAIL/E7 | UNKNOWN→BLOCK | absence_unverifiable(list_not_exhaustive) | 同処方の 40g はエタノール配合。80g のスナップショットは有効成分のみ |
| `x-c60` | jp-choco-milk | ingredient/contains | NOT lactose | S1 | FAIL/E7 | UNKNOWN→BLOCK | absence_unverifiable(claim_term_unrecognized) | 乳糖は辞書外。全粉乳は乳糖を含むが語彙では判断不能 |
| `x-c59` | us-sunscreen-1-4oz | region/pa_rating | PA++++ | S2 | FAIL/E8 | UNKNOWN→WARN | region_attribute_not_in_snapshot | US 版は PA 表示なし（属性が無いので断定不能） |

### challenge: 誤検知（正解 PASS → FAIL）2 件

| claim | 商品 | 種別 | 値 | 重大度 | 正解 | 判定 | 理由 | メモ |
|---|---|---|---|---|---|---|---|---|
| `x-c18` | jp-yk-hd12-k | bundle/includes | quick drying nozzle | S3 | PASS | FAIL→WARN | not_in_exhaustive_bundle | 同梱物名の言い換え（網羅的リスト） |
| `x-c34` | us-shampoo-floral | numeric/volume | 400 mL | S3 | PASS | FAIL→WARN | out_of_tolerance | 13.5 fl oz (400 mL) 表記の丸め |

### challenge: 真の主張への UNKNOWN 2 件

| claim | 商品 | 種別 | 値 | 重大度 | 正解 | 判定 | 理由 | メモ |
|---|---|---|---|---|---|---|---|---|
| `x-c23` | jp-choco-milk | ingredient/contains | milk solids | S1 | PASS | UNKNOWN→BLOCK | absence_unverifiable(claim_term_unrecognized) | 成分語が辞書外（全粉乳の言い換え） |
| `x-c27` | us-granola-nutfree-12oz | ingredient/contains | NOT nuts | S1 | PASS | UNKNOWN→BLOCK | absence_unverifiable(claim_term_unrecognized) | 「nuts」は辞書外の総称 |

### auto: 誤りの見逃し（正解 FAIL → FAIL 以外）0 件

（なし）

### auto: 誤検知（正解 PASS → FAIL）0 件

（なし）

### auto: 真の主張への UNKNOWN 0 件

（なし）

## 4. naive matcher の代表的な失敗（challenge）

誤検知 25 件 / 見逃し 8 件

| claim | 商品 | 種別 | 値 | 重大度 | 正解 | 判定 | 理由 | メモ |
|---|---|---|---|---|---|---|---|---|
| `x-c21` | jp-senbei-shoyu | ingredient/contains | gluten | S1 | PASS | FAIL→BLOCK | mismatch | 小麦をグルテンと言う |
| `x-c23` | jp-choco-milk | ingredient/contains | milk solids | S1 | PASS | FAIL→BLOCK | mismatch | 成分語が辞書外（全粉乳の言い換え） |
| `x-c24` | jp-choco-milk | ingredient/contains | dairy | S1 | PASS | FAIL→BLOCK | mismatch | handwritten |
| `x-c28` | us-tidewell-citrus | numeric/caffeine | 0.08 g | S1 | PASS | FAIL→BLOCK | mismatch | handwritten |
| `x-c01` | jp-kc-pd65-bk | numeric/output_power | 65 watts | S2 | PASS | FAIL→BLOCK | mismatch | 単位語 watts が辞書外 |
| `x-c03` | jp-kc-pd65-bk | compatibility/compatible_with | Nova 5 Pro | S2 | PASS | FAIL→BLOCK | mismatch | ブランド省略、網羅的でないリスト |
| `x-c04` | jp-mz-ic80-c | compatibility/compatible_with | PX-820 printer | S2 | PASS | FAIL→BLOCK | mismatch | ブランド省略＋「printer」付き、網羅的リスト |
| `x-c05` | jp-mz-ic80-c | compatibility/compatible_with | ミズナギ PX‐830F | S2 | PASS | FAIL→BLOCK | mismatch | カタカナブランド＋Unicode ハイフン |

## 5. 数字を読む際の注意（判断材料）

- **auto セットは循環的**: gold と mutation は verifier と同じ語彙（vocab.yaml）・許容誤差（tolerances.yaml）から生成しているため、決定論 v1 の auto 指標は実質的に上限値。差を見る主材料は challenge と probe。
- **challenge セットは 60 件と小さい**: 1 件で 1.5〜3 pt 動く。信頼区間は広い。
- **challenge（v0）は開発セット**: Phase 2 Step 0 で v0 の失敗事例を見て弱点修正した（色の同義語、ブランドなし型番、表記精度フロア等）。v0 の数字は上振れしている。未見の評価は results/gate-v1.md（challenge v1）を参照。
- **naive matcher の同定は文字列完全一致**: 表記揺れ（全角・GTIN-14・小文字）を含む claim は同定できず UNKNOWN になる。「同定できた claim のみ」の行は照合部分だけの比較。
- **集約ポリシー（Phase 2）**: S1 の UNKNOWN は BLOCK、S2 の UNKNOWN は WARN、S3 の UNKNOWN は PASS（`unverified_claims` に列挙）。
- 全て合成データ（架空ブランド・GS1 店内用 GTIN）。実在メーカー仕様は規約判断待ちで未取り込み（docs/SOURCES.md）。

