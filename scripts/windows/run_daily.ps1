# 毎日の定期実行本体（タスクスケジューラから呼ばれる）
# 1. 最新コードを取得  2. 巡回・判定・通知  3. data/state.json をコミットしてpush
# 失敗時は Discord に短いエラー通知を送る（Actionsの失敗メールの代わり）
param(
    [string]$RunProfile = "daily"
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Repo

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
# git の対話プロンプトで止まらないようにする
$env:GIT_TERMINAL_PROMPT = "0"

$LogDir = Join-Path $Repo "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir ("run-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))

# 30日より古いログを削除
Get-ChildItem $LogDir -Filter "run-*.log" |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

function Write-Log([string]$Message) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $Log -Value $line -Encoding UTF8
}

# 外部コマンドを実行し、標準出力・標準エラーをログへ書いて終了コードを返す
function Invoke-Logged([string]$File, [string[]]$Arguments) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $File @Arguments 2>&1 | ForEach-Object { Add-Content -Path $Log -Value "$_" -Encoding UTF8 }
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
}

function Get-EnvValue([string]$Name) {
    $envFile = Join-Path $Repo ".env"
    if (-not (Test-Path $envFile)) { return "" }
    foreach ($line in Get-Content $envFile -Encoding UTF8) {
        if ($line -match "^\s*$Name\s*=\s*(.*)$") { return $Matches[1].Trim().Trim('"') }
    }
    return ""
}

function Send-FailureNotice([string]$Reason) {
    $webhook = Get-EnvValue "DISCORD_WEBHOOK_URL"
    if (-not $webhook) { return }
    $body = @{ content = "⚠️ recruit-watcher（Surface）の定期実行が失敗しました: $Reason`nログ: $Log" } | ConvertTo-Json
    try {
        Invoke-RestMethod -Uri $webhook -Method Post -ContentType "application/json; charset=utf-8" `
            -Body ([Text.Encoding]::UTF8.GetBytes($body)) | Out-Null
    } catch {
        Write-Log "Discordへの失敗通知も送れませんでした: $_"
    }
}

$Python = Join-Path $Repo ".venv\Scripts\python.exe"
$failure = $null

try {
    Write-Log "=== 開始 (profile=$RunProfile) ==="

    # 1. 最新コードを取得（設定変更をGitHub側で行っても反映されるように）
    if ((Invoke-Logged "git" @("pull", "--rebase", "--autostash")) -ne 0) {
        Write-Log "git pull に失敗。手元のコードのまま続行します。"
    }

    # 2. 巡回・判定・通知
    $code = Invoke-Logged $Python @("src\checker.py", "--profile", $RunProfile)
    if ($code -ne 0) { $failure = "checker.py が終了コード $code で終了" }

    # 3. state.json をコミットしてpush（失敗時もActionsと同様に保存する）
    Invoke-Logged "git" @("add", "data/state.json") | Out-Null
    $hasChanges = (Invoke-Logged "git" @("diff", "--cached", "--quiet")) -ne 0
    if ($hasChanges) {
        Invoke-Logged "git" @("commit", "-m", "chore: update recruitment watch state") | Out-Null
        $pushed = $false
        foreach ($i in 1..3) {
            if ((Invoke-Logged "git" @("push")) -eq 0) { $pushed = $true; break }
            Invoke-Logged "git" @("pull", "--rebase", "--autostash") | Out-Null
            Start-Sleep -Seconds (5 * $i)
        }
        if (-not $pushed -and -not $failure) { $failure = "state.json の push に失敗" }
    } else {
        Write-Log "state.json の変更なし"
    }
} catch {
    $failure = "スクリプト例外: $_"
    Write-Log $failure
}

if ($failure) {
    Write-Log "=== 失敗: $failure ==="
    Send-FailureNotice $failure
    exit 1
}
Write-Log "=== 正常終了 ==="
exit 0
