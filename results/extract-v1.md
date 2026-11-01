# extractor v1 評価（自動生成）

> `python -m src.cli extract-eval` が生成。評価文 30 件は **SYNTHETIC**（Claude Code の手書き、実在エージェントの回答ではない）。
> 評価セット（data/extract/answers_v1.yaml）は extractor 実装前にコミット: `67ae877cbb303b21e19b7336337599d8d8c65406 2026-10-31T18:40:23+09:00`。
> ただし実装後のデバッグでこのセットの出力を参照し、実装バグ（日本語直後の数値、助詞の切り出し等）を修正した。語彙・言い回しパターンの追加はしていないが、厳密な未見評価ではない。**未見の数字は results/extract-v1-holdout.md**。

- 抽出器: `extract-rules/v1`、回答 30 件、人手ラベル 93 件、抽出 92 件
- **全体 precision 100.0% / recall 98.9%**
- coverage（1 件以上抽出できた文の割合）: 94.4%（51/54 文。主張を含まない文もあるため 100% が正解とは限らない）
- 処理時間: 9.0 ms（30 回答）

| 類型 | TP | FP（誤抽出） | FN（抽出漏れ） | precision | recall |
|---|---|---|---|---|---|
| bundle | 11 | 0 | 0 | 100.0% | 100.0% |
| compatibility | 18 | 0 | 0 | 100.0% | 100.0% |
| ingredient | 27 | 0 | 1 | 100.0% | 96.4% |
| model | 1 | 0 | 0 | 100.0% | 100.0% |
| numeric | 31 | 0 | 0 | 100.0% | 100.0% |
| region | 4 | 0 | 0 | 100.0% | 100.0% |

## 抽出 → verifier（合成 seed カタログ）

- 正しく抽出した claim の判定: BLOCK=4, PASS=87, WARN=1
- 誤抽出 claim の判定: なし
- **誤抽出が原因の BLOCK: 0 件**、WARN: 0 件

## 誤抽出・抽出漏れ

| 回答 | 種別 | 類型 | 値 | スパン | ルール | verifier |
|---|---|---|---|---|---|---|
| a20 | 抽出漏れ | ingredient | sucralose |  |  |  |
