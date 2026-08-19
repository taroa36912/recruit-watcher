#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 が見つかりません。Homebrew版: brew install python"
  exit 1
fi

PYTHON_BIN="python3"
# Homebrew等の新しいPythonがあれば優先。なければmacOS標準python3でも動作可能。
for cand in python3.13 python3.12 python3.11 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    PYTHON_BIN="$cand"
    break
  fi
done
echo "使用Python: $($PYTHON_BIN --version) ($PYTHON_BIN)"
$PYTHON_BIN -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo ".env を作成しました。APIキー等を入力してください。"
fi
python src/checker.py --validate-config
printf '\nセットアップ完了。無料テスト: ./scripts/test_mac.sh\n'
