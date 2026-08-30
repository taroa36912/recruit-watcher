# Recruit Watch for Mac

28卒向けのクオンツ、アクチュアリー、資産運用、データサイエンス、トレーダー、研究職の募集ページを巡回し、DeepSeek V4 Proで一括判定してDiscordへ通知します。

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

`--category` は繰り返し指定とカンマ区切りに対応しています。

```zsh
./scripts/run_mac.sh --category quant --no-discord
./scripts/run_mac.sh --category quant,actuary --no-discord
./scripts/run_mac.sh --category quant --category actuary --no-discord
```

## 実行プロファイル

`config/categories.yaml` の `profiles` に実行プロファイルを定義しています。カテゴリの差し替えはここだけで行えます。

```zsh
# スケジュール実行と同じ6カテゴリ（資産運用・データサイエンス・トレーダー・研究職を含む）
./scripts/run_mac.sh --profile daily --no-discord
```

`--category` を併用した場合は、プロファイルのカテゴリ一覧を上書きします。

## 全件本番実行

```zsh
./scripts/run_mac.sh
```

初回は大量通知を避けるため `notify_on_first_detection: false` にしています。一度全件実行して `data/state.json` を作った後、初回から通知したい場合のみ `true` に変更してください。

## バッチ1回POST設計

通常の実行では、ページ取得は1 target につき1回の GET を行い、取得できた全ページを1つのバッチにまとめて DeepSeek へ1回だけ POST します（`max_total_chars` を超えた場合のみ最小個数のチャンクに分割します）。DeepSeek 呼び出し前にバッチ内容を確認したい場合は次を使います（送信しません）。

```zsh
./scripts/run_mac.sh --profile daily --dry-run
```

## カテゴリと募集種別

- カテゴリは `config/categories.yaml` の `categories` が唯一の定義元です。
  - `actuary`（アクチュアリー）、`quant`（クオンツ）、`asset_management`（資産運用）、`data_science`（データサイエンス）、`trader`（トレーダー）、`research`（研究職）、`it`（IT/SIer）、`consulting`（コンサル）
  - スケジュール実行（`--profile daily`）は先頭6カテゴリのみを対象にします。`it` / `consulting` は手動実行専用です。
- 募集種別（`posting_type`）は `internship` / `newgrad` / `midcareer` / `other` / `unknown` の5種です。

## 設定ファイル

- `config/targets.yaml`: 実際に巡回する対象と、`settings` / `ai` の設定。カテゴリ重複は別レコードとして登録。
- `config/categories.yaml`: カテゴリ・募集種別・実行プロファイルの唯一の定義元。
- `config/company_catalog.yaml`: 添付DOCXから抽出したカタログ。
- `docs/category_review.md`: カテゴリ割当に自信がなく手動確認が必要な target の一覧。
- `.env`: APIキーなど。Gitにはコミットされません。

### 設定キーと既定値

`config/targets.yaml` の主なキー:

| キー | 既定値 | 説明 |
| --- | --- | --- |
| `settings.notify_posting_types` | `[internship, newgrad]` | 通知する募集種別（プロファイル側で上書き可能） |
| `settings.notify_unknown_posting_type` | `true` | 判定不能な募集種別を通知するか。見逃しを避けるため既定は true |
| `ai.max_chars_per_target` | `6000` | 1 target あたりに切り詰める本文の最大文字数 |
| `ai.max_total_chars` | `200000` | バッチ1回の最大文字数。超えたら最小個数に分割 |
| `ai.max_tokens` | `8192` | DeepSeek の応答トークン上限 |

## コマンド一覧

```zsh
python src/checker.py --validate-config
python src/checker.py --list
python src/checker.py --limit 5 --no-ai --no-discord
python src/checker.py --category quant --no-discord
python src/checker.py --target TARGET_ID --no-discord
python src/checker.py --profile daily --no-discord
python src/checker.py --profile daily --dry-run
```

## 注意

公開採用トップを登録しています。ログイン必須、JavaScript描画、アクセス拒否のページは `requests` では取得できません。その場合は対象URLの差し替え、または将来Playwright対応が必要です。自動判定結果は必ず公式ページで再確認してください。

## MacでDeepSeekが `Expecting value: line 1 column 1` になる場合

DeepSeek JSON Outputはまれに空のcontentを返すことがあります。本版では最大3回再試行し、JSON分類ではthinkingをdisabledにしています。
また、macOS標準Python 3.9/LibreSSLでurllib3 v2警告が出ないようurllib3<2を固定しています。
既存の.venvを使っている場合は `python -m pip install -r requirements.txt` を再実行してください。
