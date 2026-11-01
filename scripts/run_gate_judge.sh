#!/usr/bin/env bash
# gate-judge を実キーで 1 回実行する。キーは非表示入力で受け取り、このプロセスの中だけで使う（履歴・ファイルに残さない）。
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m src.cli gate-judge --estimate-only
printf "Paste ANTHROPIC_API_KEY (input hidden) and press Enter: "
IFS= read -rs ANTHROPIC_API_KEY
echo
if [ -z "${ANTHROPIC_API_KEY}" ]; then echo "empty key; aborted"; exit 1; fi
export ANTHROPIC_API_KEY
SPECSURE_ALLOW_NETWORK=1 exec .venv/bin/python -m src.cli gate-judge
