#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."
source .venv/bin/activate
CATEGORY="${1:-it}"
python src/checker.py --category "$CATEGORY" --no-discord
