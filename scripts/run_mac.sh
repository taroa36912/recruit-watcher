#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."
source .venv/bin/activate
python src/checker.py "$@"
