# setup.ps1 — Install claude-local on Windows
#
# Usage:
#   irm https://your-server/install/setup.ps1 | iex
#
# The server URL is auto-injected by the gateway when served over HTTP.
# Override with: $env:CLAUDE_LOCAL_URL="https://custom-url"; irm .../setup.ps1 | iex

$ErrorActionPreference = "Stop"

# __SERVER_URL__ is replaced by the gateway's nginx sub_filter with the
# actual scheme://host the user fetched from. If running locally, it stays
# as the literal placeholder.
$InjectedUrl = "__SERVER_URL__"

# Guard: build the placeholder at runtime so sub_filter can't replace it
$Placeholder = "__SERVER" + "_URL__"

# Priority: env var > injected URL
$ServerUrl = $env:CLAUDE_LOCAL_URL
if (-not $ServerUrl) {
    if ($InjectedUrl -ne $Placeholder) {
        $ServerUrl = $InjectedUrl
    }
}

$InstallDir = "$env:USERPROFILE\.local\bin"
$ScriptName = "claude-local.ps1"

Write-Host "=== Claude Local Installer ===" -ForegroundColor Cyan
Write-Host ""

if (-not $ServerUrl) {
    Write-Host "Error: Could not determine server URL." -ForegroundColor Red
    Write-Host ""
    Write-Host 'Usage: irm https://your-server/install/setup.ps1 | iex'
    return
}

# Strip trailing slash
$ServerUrl = $ServerUrl.TrimEnd('/')

Write-Host "Server: $ServerUrl"
Write-Host "Install to: $InstallDir\$ScriptName"
Write-Host ""

# 1. Check if claude CLI is installed
$claudePath = Get-Command claude -ErrorAction SilentlyContinue
if (-not $claudePath) {
    Write-Host "Claude Code CLI not found. Installing..." -ForegroundColor Yellow
    $tempFile = Join-Path $env:TEMP "install-claude.ps1"
    Invoke-WebRequest -Uri "https://claude.ai/install.ps1" -OutFile $tempFile
    & $tempFile
    Remove-Item $tempFile -Force
    Write-Host ""
}

# 2. Create install directory
if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
}

# 3. Download claude-local.ps1 script
Write-Host "Downloading claude-local.ps1..."
Invoke-WebRequest -Uri "$ServerUrl/install/claude-local.ps1" -OutFile "$InstallDir\$ScriptName"

# 4. Write/update config with server URL (preserves existing API key)
$ConfigDir = "$env:USERPROFILE\.config\claude-local"
$ConfigFile = "$ConfigDir\env"
if (-not (Test-Path $ConfigDir)) {
    New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null
}
# Read existing API key if config exists
$existingKey = ""
if (Test-Path $ConfigFile) {
    Get-Content $ConfigFile | ForEach-Object {
        if ($_ -match '^CLAUDE_LOCAL_API_KEY=(.+)$') { $existingKey = $Matches[1] }
    }
}
$lines = @("CLAUDE_LOCAL_URL=$ServerUrl")
if ($existingKey) { $lines += "CLAUDE_LOCAL_API_KEY=$existingKey" }
($lines -join "`n") | Out-File -FilePath $ConfigFile -Encoding UTF8 -NoNewline
Write-Host "Config written to $ConfigFile"

# 5. Add to user PATH if needed
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$InstallDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$InstallDir;$userPath", "User")
    $env:Path = "$InstallDir;$env:Path"
    Write-Host "Added $InstallDir to user PATH"
}

Write-Host ""
Write-Host "Installation complete!" -ForegroundColor Green
Write-Host ""
Write-Host "Get started:" -ForegroundColor Cyan
Write-Host "  claude-local.ps1 -Login    # Authenticate with your account"
Write-Host "  claude-local.ps1            # Start Claude Code with local inference"
Write-Host ""
Write-Host "If 'claude-local.ps1' is not found, restart your terminal." -ForegroundColor Yellow
