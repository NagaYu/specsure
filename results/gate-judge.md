# Gate judge — LLM judge 比較: **未実施（3 回目）**

> このファイルは実測結果ではない。LLM judge は実行されていない。

- 日付: 2026-11-01
- 状況: Phase 3 Step 0。この Claude Code セッションから参照できる環境に API キー（`ANTHROPIC_API_KEY`）が無く、
  端末タブでの手入力（`scripts/run_gate_judge.sh` の非表示入力）でもキーが入力されなかったため、API 呼び出しは 0 回。
- ユーザー判断（2026-11-01）: LLM judge 比較を飛ばして Phase 3 の残りを進める。
- 著者の方針: API 呼び出しに費用をかけない（$0）。公開時点でもこの比較は実施していない。
- 実行時の見積もり（オフライン算出）: challenge v0+v1 の 180 件 → 180 回呼び出し、入力 ~104,241 / 出力 ~72,000 トークン、
  ~$1.86（`claude-opus-5-5`、effort low）。
- 実行手順（キーを用意した時）:

  ```bash
  bash scripts/run_gate_judge.sh
  ```

  実測が取れたら、`gate-judge` コマンドがこのファイルを実測結果（呼び出し数・トークン・コスト・所要時間つき）で置き換える。

決定論 verifier と naive matcher の v0/v1 の数値は results/gate-v1.md（v1 を 1 回だけ実行した記録）を参照。
