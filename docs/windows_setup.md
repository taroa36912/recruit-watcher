# Surface（Windows）での定期実行への移行手順

GitHub Actions の毎日実行（`daily-check.yml`）を停止し、Surface のタスクスケジューラで毎日 08:15 に実行します。
処理内容は Actions と同じです（巡回 → DeepSeek判定 → Discord通知 → `data/state.json` をコミットしてpush）。

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

## 4. リポジトリを取得

```powershell
gh repo clone taroa36912/recruit-watcher "$HOME\recruit-watcher"
```

```powershell
cd "$HOME\recruit-watcher"
```

## 5. セットアップ（Actions停止 + タスク登録まで一括）

途中で `DEEPSEEK_API_KEY` と `DISCORD_WEBHOOK_URL` の入力を求められます（GitHub Secrets に登録したものと同じ値）。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\setup_windows.ps1
```

このスクリプトが行うこと:

1. `.venv` 作成と依存パッケージのインストール
2. `.env` 作成（既にあれば触らない）
3. 設定検証と dry-run（ページ取得のみ。DeepSeek・Discordへは送らず、stateも更新しない）
4. git のコミット者設定と push 権限の確認
5. `gh workflow disable daily-check.yml` で Actions の定期実行を停止
6. タスクスケジューラに `RecruitWatcher-Daily`（毎日 08:15）を登録

オプション: 実行時刻を変える `-Time "07:30"`、Actions を止めない `-SkipDisableActions`、dry-run を省く `-SkipDryRun`。

## 6. 動作確認（本番と同じ実行。DeepSeek課金・Discord通知あり）

```powershell
Start-ScheduledTask -TaskName RecruitWatcher-Daily
```

数分後に結果を確認します（`LastTaskResult` が `0` なら成功）。

```powershell
Get-ScheduledTaskInfo -TaskName RecruitWatcher-Daily | Select-Object LastRunTime, LastTaskResult, NextRunTime
```

```powershell
Get-Content (Get-ChildItem "$HOME\recruit-watcher\logs\run-*.log" | Sort-Object LastWriteTime | Select-Object -Last 1).FullName -Tail 30
```

## 7. （推奨）電源接続中はスリープしない

Surface はモダンスタンバイのため、スリープ中はタスクで起こせないことがあります。
スリープ中に予定時刻を過ぎた場合は、復帰後に自動で実行されます（`StartWhenAvailable`）。時刻どおりに実行したい場合は次を実行します。

```powershell
powercfg /change standby-timeout-ac 0
```

## 運用メモ

- ログ: `logs\run-YYYYMMDD-HHMMSS.log`（30日で自動削除）
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
