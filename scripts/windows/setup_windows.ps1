# Surface（Windows）への移行セットアップ。初回に1回だけ実行する。
#   1. Python仮想環境と依存パッケージ
#   2. .env 作成（Discord Webhook入力）
#   3. Claude Code CLI のログイン確認、設定検証と dry-run（AI・Discordへは送信しない）
#   4. git のコミット者設定
#   5. GitHub Actions の定期実行を停止
#   6. タスクスケジューラに毎日の実行を登録
param(
    [string]$Time = "08:15",
    [string]$TaskName = "RecruitWatcher-Daily",
    [switch]$SkipDisableActions,
    [switch]$SkipDryRun
)

$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Repo
$env:PYTHONUTF8 = "1"

function Step([string]$Message) { Write-Host "`n==> $Message" -ForegroundColor Cyan }
function Assert-Ok([string]$What) {
    if ($LASTEXITCODE -ne 0) { throw "$What に失敗しました（終了コード $LASTEXITCODE）" }
}

# ---------------------------------------------------------------- 1
Step "Python仮想環境を作成"
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        py -3.12 -m venv .venv
    } else {
        python -m venv .venv
    }
    Assert-Ok "venv 作成"
}
& $Python -m pip install --upgrade pip --quiet; Assert-Ok "pip 更新"
& $Python -m pip install -r requirements.txt --quiet; Assert-Ok "依存パッケージのインストール"

# ---------------------------------------------------------------- 2
Step ".env を作成"
$EnvFile = Join-Path $Repo ".env"
if (Test-Path $EnvFile) {
    Write-Host ".env は既にあるので変更しません"
} else {
    function Read-Secret([string]$Prompt) {
        $s = Read-Host $Prompt -AsSecureString
        $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
        try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b).Trim() }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
    }
    $discord = Read-Secret "DISCORD_WEBHOOK_URL を貼り付けてEnter"
    $content = @"
DISCORD_WEBHOOK_URL=$discord
# 判定AI: claude_cli（Claude Code / Proサブスクリプション）または deepseek
AI_BACKEND=claude_cli
GRADUATION_YEAR=2028
"@
    # python-dotenv がBOMを誤読しないよう BOM なし UTF-8 で書く
    [IO.File]::WriteAllText($EnvFile, $content, (New-Object Text.UTF8Encoding $false))
    Write-Host ".env を作成しました"
}

# ---------------------------------------------------------------- 3
Step "Claude Code CLI のログインを確認（ごく短い呼び出しを1回行う）"
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    $local = Join-Path $HOME ".local\bin"
    if (Test-Path (Join-Path $local "claude.exe")) { $env:Path = "$local;$env:Path" }
}
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    throw "claude コマンドが見つかりません。手順書の「Claude Code のインストール」を先に実行してください"
}
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$ping = "Reply with OK only." | claude -p --output-format json --model haiku | Out-String
try { $res = $ping | ConvertFrom-Json } catch { throw "claude CLI の応答を読めませんでした: $ping" }
if ($res.is_error) { throw "claude CLI がエラーを返しました（未ログインの可能性。claude を起動して /login してください）: $($res.result)" }
Write-Host "claude CLI OK: $($res.result)"

Step "設定を検証"
& $Python src\checker.py --validate-config; Assert-Ok "設定検証"
if (-not $SkipDryRun) {
    Step "dry-run（ページ取得のみ。AI・Discordへは送信せず、stateも更新しない）"
    & $Python src\checker.py --profile daily --dry-run; Assert-Ok "dry-run"
}

# ---------------------------------------------------------------- 4
Step "git のコミット者を設定（このリポジトリのみ）"
$login = gh api user --jq .login; Assert-Ok "gh api user"
$id = gh api user --jq .id; Assert-Ok "gh api user"
git config user.name "$login (Surface)"
git config user.email "$id+$login@users.noreply.github.com"
gh auth setup-git; Assert-Ok "gh auth setup-git"
git push --dry-run; Assert-Ok "push 権限の確認"

# ---------------------------------------------------------------- 5
if (-not $SkipDisableActions) {
    Step "GitHub Actions の定期実行を停止"
    gh workflow disable daily-check.yml; Assert-Ok "gh workflow disable"
    gh workflow list --all
}

# ---------------------------------------------------------------- 6
Step "タスクスケジューラに登録（毎日 $Time）"
$RunScript = Join-Path $Repo "scripts\windows\run_daily.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$RunScript`"" `
    -WorkingDirectory $Repo
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 10)
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description "recruit-watcher: 28卒採用ページ巡回（旧GitHub Actions daily-check）" -Force | Out-Null

Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo |
    Select-Object TaskName, NextRunTime | Format-List

Write-Host "`nセットアップ完了。" -ForegroundColor Green
Write-Host "今すぐ1回試す:  Start-ScheduledTask -TaskName $TaskName"
Write-Host "ログ:           $Repo\logs\"
