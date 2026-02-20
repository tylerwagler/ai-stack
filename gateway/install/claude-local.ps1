# claude-local.ps1 — Launch Claude Code against a local AI stack
#
# Uses Open WebUI for authentication and LiteLLM (via gateway) for inference.
# All traffic goes through a single port (gateway on :3000).
#
# Install: irm https://YOUR-HOST:3000/install/setup.ps1 | iex
#
# Usage:
#   .\claude-local.ps1 [args]                    # Launch Claude Code
#   .\claude-local.ps1 -Login                    # Login and retrieve/create API key
#   .\claude-local.ps1 -SetKey <api-key>         # Set API key manually
#   .\claude-local.ps1 -SetUrl <base-url>        # Set base URL
#   .\claude-local.ps1 -Config                   # Show current config
#   .\claude-local.ps1 -Update                   # Update this script from the server
#   .\claude-local.ps1 -ResetConfig              # Reset all config
#   .\claude-local.ps1 -Version                  # Show version

param(
    [switch]$Login,
    [string]$SetKey,
    [string]$SetUrl,
    [switch]$Config,
    [switch]$Update,
    [switch]$ResetConfig,
    [switch]$Help,
    [switch]$Version,
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$ClaudeArgs
)

$ScriptVersion = "2.1.5"
$ConfigDir = "$env:USERPROFILE\.config\claude-local"
$ConfigFile = "$ConfigDir\env"

# --- Config management ---

function Save-Config {
    if (-not (Test-Path $ConfigDir)) {
        New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null
    }
    $lines = @()
    if ($script:ApiKey) { $lines += "CLAUDE_LOCAL_API_KEY=$($script:ApiKey)" }
    if ($script:BaseUrl) { $lines += "CLAUDE_LOCAL_URL=$($script:BaseUrl)" }
    $lines -join "`n" | Out-File -FilePath $ConfigFile -Encoding UTF8 -NoNewline
}

function Load-Config {
    $script:ApiKey = $null
    $script:BaseUrl = $null
    if (Test-Path $ConfigFile) {
        Get-Content $ConfigFile | ForEach-Object {
            if ($_ -match '^(\w+)=(.+)$') {
                switch ($Matches[1]) {
                    'CLAUDE_LOCAL_API_KEY' { $script:ApiKey = $Matches[2] }
                    'CLAUDE_LOCAL_URL' { $script:BaseUrl = $Matches[2] }
                }
            }
        }
    }
}

function Ensure-Url {
    if (-not $script:BaseUrl) {
        $script:BaseUrl = Read-Host "Server URL (e.g. http://10.20.10.5:3000)"
        if (-not $script:BaseUrl) {
            Write-Host "Error: URL is required." -ForegroundColor Red
            exit 1
        }
        $script:BaseUrl = $script:BaseUrl.TrimEnd('/')
        Save-Config
    }
}

# --- Open WebUI authentication ---

function Get-ApiKeyFromLogin {
    param(
        [string]$Email,
        [securestring]$Password,
        [string]$Url
    )

    $BSTR = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($Password)
    $plainPassword = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR)

    $body = @{ email = $Email; password = $plainPassword } | ConvertTo-Json

    Write-Host "Authenticating with Open WebUI..."

    # Step 1: Sign in
    try {
        $response = Invoke-RestMethod -Uri "$Url/api/v1/auths/signin" `
            -Method Post `
            -Headers @{ "Content-Type" = "application/json" } `
            -Body $body `
            -ErrorAction Stop
    }
    catch {
        Write-Host "Authentication failed: $($_.Exception.Message)" -ForegroundColor Red
        return $null
    }

    if (-not $response.token) {
        $detail = if ($response.detail) { $response.detail } else { "Authentication failed" }
        Write-Host "Authentication failed: $detail" -ForegroundColor Red
        return $null
    }

    $token = $response.token
    Write-Host "Authentication successful! Checking API key..." -ForegroundColor Green

    # Step 2: Try to get existing API key
    try {
        $keyResp = Invoke-RestMethod -Uri "$Url/api/v1/auths/api_key" `
            -Method Get `
            -Headers @{ "Authorization" = "Bearer $token" } `
            -ErrorAction Stop

        if ($keyResp.api_key -and $keyResp.api_key -ne "null") {
            Write-Host "Found existing API key." -ForegroundColor Green
            return $keyResp.api_key
        }
    }
    catch {
        # No existing key, create one
    }

    # Step 3: Create a new API key
    Write-Host "Creating new API key..." -ForegroundColor Yellow
    try {
        $createResp = Invoke-RestMethod -Uri "$Url/api/v1/auths/api_key" `
            -Method Post `
            -Headers @{
                "Authorization" = "Bearer $token"
                "Content-Type" = "application/json"
            } `
            -ErrorAction Stop

        if ($createResp.api_key) {
            Write-Host "API key created successfully." -ForegroundColor Green
            return $createResp.api_key
        }
    }
    catch {
        Write-Host "Failed to create API key: $($_.Exception.Message)" -ForegroundColor Red
    }

    return $null
}

# --- Load config ---
Load-Config

# --- Handle commands ---

if ($Version) {
    Write-Host "claude-local v$ScriptVersion"
    exit 0
}

if ($Help) {
    Write-Host "claude-local v$ScriptVersion — Launch Claude Code with local AI inference" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Usage: .\claude-local.ps1 [options] [claude args...]"
    Write-Host ""
    Write-Host "Options:"
    Write-Host "  -Login           Login to Open WebUI and save API key"
    Write-Host "  -SetKey <key>    Set API key manually"
    Write-Host "  -SetUrl <url>    Set gateway URL"
    Write-Host "  -Config          Show current configuration"
    Write-Host "  -Update          Update this script from the server"
    Write-Host "  -ResetConfig     Delete saved configuration"
    Write-Host "  -Version         Show version"
    Write-Host "  -Help            Show this help"
    Write-Host ""
    Write-Host "All other arguments are passed to 'claude' directly."
    exit 0
}

if ($SetKey) {
    $script:ApiKey = $SetKey
    Save-Config
    Write-Host "API key saved to $ConfigFile" -ForegroundColor Green
    exit 0
}

if ($SetUrl) {
    $script:BaseUrl = $SetUrl.TrimEnd('/')
    Save-Config
    Write-Host "Base URL saved to $ConfigFile" -ForegroundColor Green
    exit 0
}

if ($Config) {
    Write-Host "Configuration ($ConfigFile):" -ForegroundColor Cyan
    if ($script:ApiKey) {
        $preview = $script:ApiKey.Substring(0, [Math]::Min(12, $script:ApiKey.Length))
        Write-Host "  API Key: ${preview}... (set)" -ForegroundColor Green
    } else {
        Write-Host "  API Key: (not set)" -ForegroundColor Yellow
    }
    Write-Host "  URL: $(if ($script:BaseUrl) { $script:BaseUrl } else { '(not set)' })"
    exit 0
}

if ($ResetConfig) {
    if (Test-Path $ConfigFile) {
        Remove-Item $ConfigFile
        Write-Host "Configuration reset." -ForegroundColor Green
    } else {
        Write-Host "No configuration file found."
    }
    exit 0
}

if ($Update) {
    Ensure-Url
    Write-Host "Updating claude-local.ps1 from $($script:BaseUrl)..."
    $scriptPath = $MyInvocation.MyCommand.Path
    try {
        Invoke-WebRequest -Uri "$($script:BaseUrl)/install/claude-local.ps1" -OutFile $scriptPath -ErrorAction Stop
        Write-Host "Updated successfully." -ForegroundColor Green
    }
    catch {
        Write-Host "Update failed: $($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }
    exit 0
}

if ($Login) {
    Ensure-Url
    Write-Host "Login to AI Stack" -ForegroundColor Cyan
    Write-Host ""

    $email = Read-Host "Email"
    if (-not $email) {
        Write-Host "Error: Email required" -ForegroundColor Red
        exit 1
    }

    $password = Read-Host "Password" -AsSecureString

    $retrievedKey = Get-ApiKeyFromLogin -Email $email -Password $password -Url $script:BaseUrl

    if ($retrievedKey) {
        $script:ApiKey = $retrievedKey
        Save-Config
        Write-Host ""
        Write-Host "API key configured successfully!" -ForegroundColor Green
        Write-Host "  Config saved to: $ConfigFile"
        Write-Host ""
        Write-Host "Run 'claude-local.ps1' to start Claude Code."
    } else {
        Write-Host "Failed to retrieve or create API key." -ForegroundColor Red
        exit 1
    }
    exit 0
}

# --- Ensure URL is set ---
Ensure-Url

# --- Auto-update ---
if ($env:CLAUDE_LOCAL_SKIP_UPDATE -ne "1") {
    try {
        $versionUrl = "$($script:BaseUrl)/install/version"
        $remoteVersion = (Invoke-RestMethod -Uri $versionUrl `
            -TimeoutSec 5 -ErrorAction Stop).Trim()
        if ($remoteVersion -and $remoteVersion -ne $ScriptVersion) {
            Write-Host "Updating claude-local.ps1 ($ScriptVersion -> $remoteVersion)..." -ForegroundColor Yellow
            $scriptPath = $MyInvocation.MyCommand.Path
            if (-not $scriptPath) {
                $scriptPath = (Get-Command claude-local.ps1 -ErrorAction SilentlyContinue).Source
            }
            if (-not $scriptPath) {
                Write-Host "Auto-update failed: cannot determine script path." -ForegroundColor Yellow
            } else {
                try {
                    Invoke-WebRequest -Uri "$($script:BaseUrl)/install/claude-local.ps1" `
                        -OutFile "$scriptPath.tmp" -ErrorAction Stop
                    if ((Get-Item "$scriptPath.tmp").Length -gt 0) {
                        Move-Item "$scriptPath.tmp" $scriptPath -Force
                        Write-Host "Updated. Restarting..." -ForegroundColor Green
                        $env:CLAUDE_LOCAL_SKIP_UPDATE = "1"
                        & $scriptPath @ClaudeArgs
                        exit $LASTEXITCODE
                    } else {
                        Remove-Item "$scriptPath.tmp" -Force -ErrorAction SilentlyContinue
                        Write-Host "Auto-update failed: empty download." -ForegroundColor Yellow
                    }
                } catch {
                    Remove-Item "$scriptPath.tmp" -Force -ErrorAction SilentlyContinue
                    Write-Host "Auto-update download failed: $($_.Exception.Message)" -ForegroundColor Yellow
                }
            }
        }
    } catch {
        Write-Host "Update check failed: $($_.Exception.Message)" -ForegroundColor DarkGray
    }
}
Remove-Item Env:CLAUDE_LOCAL_SKIP_UPDATE -ErrorAction SilentlyContinue

Write-Host "claude-local v$ScriptVersion" -ForegroundColor Cyan

# --- Ensure we have an API key ---

if (-not $script:ApiKey) {
    Write-Host ""
    Write-Host "Welcome to Claude Local!" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "No API key found. You need to authenticate first." -ForegroundColor Yellow
    Write-Host ""

    $resp = Read-Host "Would you like to log in now? (y/n)"

    if ($resp -match '^[Yy]') {
        Write-Host ""
        $email = Read-Host "Email"
        if (-not $email) {
            Write-Host "Error: Email required" -ForegroundColor Red
            exit 1
        }

        $password = Read-Host "Password" -AsSecureString
        $retrievedKey = Get-ApiKeyFromLogin -Email $email -Password $password -Url $script:BaseUrl

        if ($retrievedKey) {
            $script:ApiKey = $retrievedKey
            Save-Config
            Write-Host ""
            Write-Host "API key configured! Continuing..." -ForegroundColor Green
            Write-Host ""
        } else {
            exit 1
        }
    } else {
        Write-Host ""
        Write-Host "Set up later with: .\claude-local.ps1 -Login" -ForegroundColor Yellow
        exit 1
    }
}

if (-not $script:ApiKey) {
    Write-Host "Error: No API key. Run: .\claude-local.ps1 -Login" -ForegroundColor Red
    exit 1
}

# --- Validate API key ---
Write-Host "Validating API key..." -ForegroundColor Cyan

try {
    $null = Invoke-WebRequest -Uri "$($script:BaseUrl)/v1/models" `
        -Headers @{ "x-api-key" = $script:ApiKey } `
        -Method Get -UseBasicParsing -ErrorAction Stop

    Write-Host "API key valid" -ForegroundColor Green
    Write-Host ""
}
catch {
    $statusCode = $_.Exception.Response.StatusCode.value__
    if ($statusCode -eq 401 -or $statusCode -eq 403) {
        Write-Host "API key invalid or expired." -ForegroundColor Red
        $resp = Read-Host "Log in again? (y/n)"
        if ($resp -match '^[Yy]') {
            $email = Read-Host "Email"
            $password = Read-Host "Password" -AsSecureString
            $retrievedKey = Get-ApiKeyFromLogin -Email $email -Password $password -Url $script:BaseUrl
            if ($retrievedKey) {
                $script:ApiKey = $retrievedKey
                Save-Config
            } else { exit 1 }
        } else { exit 1 }
    } else {
        Write-Host "Warning: Could not validate API key (HTTP $statusCode). Continuing..." -ForegroundColor Yellow
    }
}

# --- Set environment for Claude Code ---
Remove-Item Env:ANTHROPIC_API_KEY -ErrorAction SilentlyContinue
$env:ANTHROPIC_AUTH_TOKEN = $script:ApiKey
$env:ANTHROPIC_BASE_URL = $script:BaseUrl
$env:CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = "1"

# --- Default model ---
$hasModel = $false
foreach ($arg in $ClaudeArgs) {
    if ($arg -eq "--model") {
        $hasModel = $true
        break
    }
}

if (-not $hasModel) {
    $ClaudeArgs = @("--model", "Qwen3 Coder Next") + $ClaudeArgs
}

# --- Launch Claude Code ---
& claude @ClaudeArgs
