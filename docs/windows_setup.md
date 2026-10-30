# Surface（Windows）での定期実行への移行手順

GitHub Actions の毎日実行（`daily-check.yml`）を停止し、Surface のタスクスケジューラで毎日 08:15 に実行します。
処理内容: 巡回 → **Claude Code CLI（Claude Pro のログイン済みセッション）で判定** → Discord通知 → `data/state.json` をコミットしてpush。
APIキーは使いません（従量課金なし。Pro の利用枠を消費します）。

以下はすべて **Surface 上の PowerShell**（通常権限で可）で、上から順に実行します。

## 1. 必要なツールをインストール

```powershell
winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements
winget install -e --id GitHub.cli --accept-source-agreements --accept-package-agreements
```

インストール直後のウィンドウでも使えるよう PATH を読み直します。

```powershell
$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
```

## 1.5 Claude Code のインストールとログイン

```powershell
irm https://claude.ai/install.ps1 | iex
```

```powershell
$env:Path = "$HOME\.local\bin;" + $env:Path
```

対話画面が開いたら `/login` で **Claude Pro のアカウント**でログインし、`/exit` で閉じます（初回のみ）。

```powershell
claude
```

## 2. GitHub にログイン

ブラウザが開くので承認します。

```powershell
gh auth login --hostname github.com --git-protocol https --web
```

## 3. Windows用スクリプトを main に取り込む（未マージの場合のみ）

```powershell
gh pr create -R taroa36912/recruit-watcher --base main --head claude/ecstatic-faraday-ac8p0i --title "Surface(Windows)での定期実行に移行" --body "scripts/windows を追加"
```

```powershell
gh pr merge -R taroa36912/recruit-watcher claude/ecstatic-faraday-ac8p0i --merge
```

Job-hunting 側の `.gitignore`（`recruit-watcher/` を除外する変更）も取り込みます。

```powershell
gh pr create -R taroa36912/Job-hunting --base main --head claude/ecstatic-faraday-ac8p0i --title "recruit-watcher フォルダを git 管理から除外" --body ".gitignore に recruit-watcher/ を追加"
```

```powershell
gh pr merge -R taroa36912/Job-hunting claude/ecstatic-faraday-ac8p0i --merge
```

## 4. リポジトリを取得（Job-hunting フォルダの下に配置）

配置先は `<Job-hunting>\recruit-watcher` です。まず Surface 上の Job-hunting フォルダの場所を変数に入れます（場所が違う場合はこの1行だけ書き換えてください）。

```powershell
$JobHunting = "$HOME\Job-hunting"
```

Job-hunting がまだ Surface に無い場合のみ、先に取得します。

```powershell
if (-not (Test-Path $JobHunting)) { gh repo clone taroa36912/Job-hunting $JobHunting }
```

recruit-watcher を Job-hunting の中に取得して移動します。

```powershell
gh repo clone taroa36912/recruit-watcher "$JobHunting\recruit-watcher"
```

```powershell
cd "$JobHunting\recruit-watcher"
```

`recruit-watcher` は独立した git リポジトリのままです（Job-hunting 側の `.gitignore` で除外済み）。Job-hunting にコミットされることはありません。

## 5. セットアップ（Actions停止 + タスク登録まで一括）

途中で `DISCORD_WEBHOOK_URL` の入力を求められます（GitHub Secrets に登録したものと同じ値）。DeepSeek のキーは不要です。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\setup_windows.ps1
```

このスクリプトが行うこと:

1. `.venv` 作成と依存パッケージのインストール
2. `.env` 作成（既にあれば触らない）
3. Claude Code CLI のログイン確認（haiku に1回だけ短く問い合わせ）、設定検証、dry-run（ページ取得のみ。AI・Discordへは送らず、stateも更新しない）
4. git のコミット者設定と push 権限の確認
5. `gh workflow disable daily-check.yml` で Actions の定期実行を停止
6. タスクスケジューラに `RecruitWatcher-Daily`（毎日 08:15）を登録

オプション: 実行時刻を変える `-Time "07:30"`、Actions を止めない `-SkipDisableActions`、dry-run を省く `-SkipDryRun`。

## 6. 動作確認（本番と同じ実行。Proの利用枠を消費・Discord通知あり）

```powershell
Start-ScheduledTask -TaskName RecruitWatcher-Daily
```

数分後に結果を確認します（`LastTaskResult` が `0` なら成功）。

```powershell
Get-ScheduledTaskInfo -TaskName RecruitWatcher-Daily | Select-Object LastRunTime, LastTaskResult, NextRunTime
```

```powershell
Get-Content (Get-ChildItem "$JobHunting\recruit-watcher\logs\run-*.log" | Sort-Object LastWriteTime | Select-Object -Last 1).FullName -Tail 30
```

## 7. （推奨）電源接続中はスリープしない

Surface はモダンスタンバイのため、スリープ中はタスクで起こせないことがあります。
スリープ中に予定時刻を過ぎた場合は、復帰後に自動で実行されます（`StartWhenAvailable`）。時刻どおりに実行したい場合は次を実行します。

```powershell
powercfg /change standby-timeout-ac 0
```

## Claude 判定の設定

`config/targets.yaml` の `ai` で変更できます（`.env` の同名環境変数が優先）。

| 項目 | 既定値 | 説明 |
| --- | --- | --- |
| `ai.backend` / `AI_BACKEND` | `claude_cli` | `deepseek` にすると従来の DeepSeek API に戻る |
| `ai.claude_cli.model` / `CLAUDE_MODEL` | `sonnet` | 利用枠を節約したい場合は `haiku` |
| `ai.claude_cli.max_chars_per_target` | `3000` | 1社あたりの本文上限（DeepSeek時は6000） |
| `ai.claude_cli.max_total_chars` | `80000` | 1回の呼び出しの上限。超えたら複数回に分割 |
| `ai.claude_cli.timeout_seconds` | `900` | 1回の呼び出しのタイムアウト |

Pro の利用上限に達した場合はその回の判定がエラーになり、Discord に失敗通知が届きます（翌日は通常どおり実行）。

## 運用メモ

- 配置先: `<Job-hunting>\recruit-watcher`（フォルダを移動した場合は `setup_windows.ps1` を再実行してタスクを登録し直す）
- ログ: `<Job-hunting>\recruit-watcher\logs\run-YYYYMMDD-HHMMSS.log`（30日で自動削除）
- 失敗時は Discord に「⚠️ 定期実行が失敗しました」と通知されます
- 実行のたびに `git pull` するので、設定変更は GitHub 側でコミットすれば次回から反映されます
- タスクは「ユーザーがログオンしているときのみ実行」です。Surface をサインアウトした状態にしないでください

## 元に戻す（Actions に戻す）

```powershell
Unregister-ScheduledTask -TaskName RecruitWatcher-Daily -Confirm:$false
```

```powershell
gh workflow enable daily-check.yml -R taroa36912/recruit-watcher
```
