# Recruit Watch for Mac

28卒向けのクオンツ、アクチュアリー、IT/SIer、コンサルの募集ページを巡回し、DeepSeek V4 Proで判定してDiscordへ通知します。

## Macでの初期設定

```zsh
cd ~/Downloads/recruit-watch-mac
chmod +x scripts/*.sh
./scripts/setup_mac.sh
open -e .env
```

`.env` に `DEEPSEEK_API_KEY` と `DISCORD_WEBHOOK_URL` を入力します。

## 無料ローカルテスト

DeepSeek APIもDiscordも使わず、先頭3件だけ接続確認します。

```zsh
./scripts/test_mac.sh
```

件数を変える場合:

```zsh
source .venv/bin/activate
python src/checker.py --limit 10 --no-ai --no-discord
```

## DeepSeekを使う1件テスト

```zsh
./scripts/run_mac.sh --target google-it-75-google-softwareengineer --no-discord
```

対象IDは次で確認できます。

```zsh
./scripts/run_mac.sh --list
```

## 分野別テスト

```zsh
./scripts/run_mac.sh --category quant --no-discord
./scripts/run_mac.sh --category actuary --no-discord
./scripts/run_mac.sh --category it --no-discord
./scripts/run_mac.sh --category consulting --no-discord
```

## 全件本番実行

```zsh
./scripts/run_mac.sh
```

初回は大量通知を避けるため `notify_on_first_detection: false` にしています。一度全件実行して `data/state.json` を作った後、初回から通知したい場合のみ `true` に変更してください。

## 設定ファイル

- `config/targets.yaml`: 実際に巡回する対象。カテゴリ重複は別レコードとして登録。
- `config/company_catalog.yaml`: 添付DOCXから抽出した全270行のカタログ。
- `.env`: APIキーなど。Gitにはコミットされません。

## コマンド一覧

```zsh
python src/checker.py --validate-config
python src/checker.py --list
python src/checker.py --limit 5 --no-ai --no-discord
python src/checker.py --category quant --no-discord
python src/checker.py --target TARGET_ID --no-discord
```

## 注意

公開採用トップを登録しています。ログイン必須、JavaScript描画、アクセス拒否のページは `requests` では取得できません。その場合は対象URLの差し替え、または将来Playwright対応が必要です。自動判定結果は必ず公式ページで再確認してください。


## MacでDeepSeekが `Expecting value: line 1 column 1` になる場合
DeepSeek JSON Outputはまれに空のcontentを返すことがあります。本版では最大3回再試行し、JSON分類ではthinkingをdisabledにしています。
また、macOS標準Python 3.9/LibreSSLでurllib3 v2警告が出ないようurllib3<2を固定しています。
既存の.venvを使っている場合は `python -m pip install -r requirements.txt` を再実行してください。
