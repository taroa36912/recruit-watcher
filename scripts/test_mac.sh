#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."
source .venv/bin/activate
# 最初の3件だけ、DeepSeekとDiscordを使わずに接続・解析テスト
python src/checker.py --limit 3 --no-ai --no-discord
