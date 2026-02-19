# claude-local.ps1 — Local development wrapper for Claude Code
#
# This version is for development use when running near the AI Stack host.
# For distributable installs, use:
#   $env:PORTAL_URL="http://ellie:3000"; irm http://ellie:3000/install/setup.ps1 | iex
#
# Usage:
#   .\claude-local.ps1 [args]
#   .\claude-local.ps1 -Login                  # Login and retrieve/create API key
#   .\claude-local.ps1 -SetKey <api-key>       # Set API key manually
#   .\claude-local.ps1 -SetUrl <base-url>      # Set base URL
#   .\claude-local.ps1 -Config                 # Show current config
#   .\claude-local.ps1 -ResetConfig            # Reset configuration

param(
    [switch]$Login,
    [string]$SetKey,
    [string]$SetUrl,
    [switch]$Config,
    [switch]$ResetConfig,
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$ClaudeArgs
)

# Configuration
$ConfigDir = "$env:USERPROFILE\.config\claude-local"
$ConfigFile = "$ConfigDir\config.ps1"
$DefaultBaseUrl = "http://10.20.10.5:8081"

# Helper function to save configuration
function Save-Config {
    if (-not (Test-Path $ConfigDir)) {
        New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null
    }
    $lines = @()
    if ($script:ApiKey) {
        $lines += "`$env:CLAUDE_LOCAL_API_KEY = `"$($script:ApiKey)`""
    }
    if ($script:BaseUrl) {
        $lines += "`$env:CLAUDE_LOCAL_URL = `"$($script:BaseUrl)`""
    }
    $lines -join "`n" | Out-File -FilePath $ConfigFile -Encoding UTF8
}

# Helper function to authenticate via ai-proxy auth endpoints
function Get-ApiKeyFromLogin {
    param(
        [string]$Email,
        [securestring]$Password,
        [string]$BaseUrl
    )

    # Convert SecureString to plain text
    $BSTR = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($Password)
    $plainPassword = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR)

    $body = @{ email = $Email; password = $plainPassword } | ConvertTo-Json

    Write-Host "Authenticating with AI Stack..."

    try {
        $response = Invoke-RestMethod -Uri "$BaseUrl/v1/auth/login" `
            -Method Post `
            -Headers @{ "Content-Type" = "application/json" } `
            -Body $body `
            -ErrorAction Stop
    }
    catch {
        Write-Host "Authentication failed: $($_.Exception.Message)" -ForegroundColor Red
        return $null
    }

    if (-not $response.access_token) {
        Write-Host "Authentication failed." -ForegroundColor Red
        return $null
    }

    $accessToken = $response.access_token
    $keys = $response.api_keys

    Write-Host "Authentication successful!" -ForegroundColor Green

    if ($keys -and $keys.Count -gt 0) {
        Write-Host "`nFound $($keys.Count) existing API key(s):" -ForegroundColor Cyan
        for ($i = 0; $i -lt $keys.Count; $i++) {
            $k = $keys[$i]
            $num = $i + 1
            $created = if ($k.created_at) { $k.created_at.ToString("yyyy-MM-dd") } else { "unknown" }
            Write-Host "  $num. $($k.name) (created: $created)"
        }

        Write-Host "`nSelect a key to use (1-$($keys.Count)), or press Enter to create a new one: " -NoNewline
        $selection = Read-Host

        if ($selection -match '^\d+$' -and [int]$selection -ge 1 -and [int]$selection -le $keys.Count) {
            Write-Host "Using existing API key." -ForegroundColor Green
            return $keys[[int]$selection - 1].api_key
        }
    }

    # Create new API key
    Write-Host "Creating new API key..." -ForegroundColor Yellow
    $keyName = "claude-local-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    $createBody = @{ name = $keyName } | ConvertTo-Json

    try {
        $createResp = Invoke-RestMethod -Uri "$BaseUrl/v1/auth/keys" `
            -Method Post `
            -Headers @{
                "Authorization" = "Bearer $accessToken"
                "Content-Type" = "application/json"
            } `
            -Body $createBody `
            -ErrorAction Stop

        Write-Host "Successfully created new API key: $keyName" -ForegroundColor Green
        return $createResp.api_key
    }
    catch {
        Write-Host "Failed to create API key: $($_.Exception.Message)" -ForegroundColor Red
        return $null
    }
}

# Load existing configuration
if (Test-Path $ConfigFile) {
    . $ConfigFile
    $script:ApiKey = $env:CLAUDE_LOCAL_API_KEY
    $script:BaseUrl = $env:CLAUDE_LOCAL_URL
    # Clean up
    Remove-Item Env:\CLAUDE_LOCAL_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:\CLAUDE_LOCAL_URL -ErrorAction SilentlyContinue
}

# Handle configuration commands
if ($SetKey) {
    $script:ApiKey = $SetKey
    Save-Config
    Write-Host "API key saved to $ConfigFile" -ForegroundColor Green
    exit 0
}

if ($SetUrl) {
    $script:BaseUrl = $SetUrl
    Save-Config
    Write-Host "Base URL saved to $ConfigFile" -ForegroundColor Green
    exit 0
}

if ($Config) {
    Write-Host "Current configuration ($ConfigFile):" -ForegroundColor Cyan
    if ($script:ApiKey) {
        Write-Host "  API Key: $($script:ApiKey.Substring(0, [Math]::Min(10, $script:ApiKey.Length)))... (set)" -ForegroundColor Green
    } else {
        Write-Host "  API Key: (not set)" -ForegroundColor Yellow
    }
    if ($script:BaseUrl) {
        Write-Host "  Base URL: $($script:BaseUrl)"
    } else {
        Write-Host "  Base URL: $DefaultBaseUrl (default)"
    }
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

if ($Login) {
    Write-Host "Login to AI Stack" -ForegroundColor Cyan
    Write-Host ""

    $email = Read-Host "Email"
    if (-not $email) {
        Write-Host "Error: Email required" -ForegroundColor Red
        exit 1
    }

    $password = Read-Host "Password" -AsSecureString

    $authUrl = if ($script:BaseUrl) { $script:BaseUrl } else { $DefaultBaseUrl }
    $retrievedKey = Get-ApiKeyFromLogin -Email $email -Password $password -BaseUrl $authUrl

    if ($retrievedKey) {
        $script:ApiKey = $retrievedKey
        Save-Config
        Write-Host ""
        Write-Host "API key configured successfully!" -ForegroundColor Green
        Write-Host "  Config saved to: $ConfigFile"
        Write-Host ""
        Write-Host "You can now use: .\claude-local.ps1 `"Your prompt`""
    } else {
        Write-Host "Failed to retrieve or create API key." -ForegroundColor Red
        exit 1
    }
    exit 0
}

# Check for API key
if (-not $script:ApiKey) {
    Write-Host ""
    Write-Host "Welcome to Claude Local!" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "No API key found." -ForegroundColor Yellow
    Write-Host ""

    $response = Read-Host "Would you like to log in now? (y/n)"

    if ($response -match '^[Yy]') {
        Write-Host ""

        $email = Read-Host "Email"
        if (-not $email) {
            Write-Host "Error: Email required" -ForegroundColor Red
            exit 1
        }

        $password = Read-Host "Password" -AsSecureString
        $authUrl = if ($script:BaseUrl) { $script:BaseUrl } else { $DefaultBaseUrl }
        $retrievedKey = Get-ApiKeyFromLogin -Email $email -Password $password -BaseUrl $authUrl

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
        Write-Host "Run: .\claude-local.ps1 -Login" -ForegroundColor Yellow
        exit 1
    }
}

# Set environment variables
$env:ANTHROPIC_API_KEY = $script:ApiKey
$env:ANTHROPIC_BASE_URL = if ($script:BaseUrl) { $script:BaseUrl } else { $DefaultBaseUrl }
$env:CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = "1"

# Validate API key
Write-Host "Validating API key..." -ForegroundColor Cyan

try {
    $null = Invoke-WebRequest -Uri "$env:ANTHROPIC_BASE_URL/v1/models" `
        -Headers @{ "Authorization" = "Bearer $($script:ApiKey)" } `
        -Method Get -UseBasicParsing -ErrorAction Stop

    Write-Host "API key valid" -ForegroundColor Green
    Write-Host ""
}
catch {
    $statusCode = $_.Exception.Response.StatusCode.value__
    if ($statusCode -eq 401 -or $statusCode -eq 403) {
        Write-Host "API key invalid or expired." -ForegroundColor Red
        $response = Read-Host "Log in again? (y/n)"
        if ($response -match '^[Yy]') {
            $email = Read-Host "Email"
            $password = Read-Host "Password" -AsSecureString
            $authUrl = if ($script:BaseUrl) { $script:BaseUrl } else { $DefaultBaseUrl }
            $retrievedKey = Get-ApiKeyFromLogin -Email $email -Password $password -BaseUrl $authUrl
            if ($retrievedKey) {
                $script:ApiKey = $retrievedKey
                Save-Config
                $env:ANTHROPIC_API_KEY = $script:ApiKey
            } else { exit 1 }
        } else { exit 1 }
    } else {
        Write-Host "Warning: Could not validate API key (HTTP $statusCode). Continuing..." -ForegroundColor Yellow
    }
}

# Model selection
$hasModel = $false
foreach ($arg in $ClaudeArgs) {
    if ($arg -eq "--model" -or $arg -eq "--help" -or $arg -eq "-h" -or $arg -eq "--version" -or $arg -eq "-v") {
        $hasModel = $true
        break
    }
}

if (-not $hasModel) {
    Write-Host "Fetching available models..." -ForegroundColor Cyan
    try {
        # Use public chat-models endpoint (no auth needed)
        $modelsResp = Invoke-RestMethod -Uri "$env:ANTHROPIC_BASE_URL/model/chat-models" `
            -Method Get -ErrorAction Stop

        if ($modelsResp -and $modelsResp.Count -gt 0) {
            if ($modelsResp.Count -eq 1) {
                $m = $modelsResp[0]
                $hostLabel = if ($m.is_local) { "Ellie" } else { "Sparky" }
                $displayName = if ($m.alias) { $m.alias } else { $m.id }
                Write-Host "Using model: $displayName ($hostLabel)" -ForegroundColor Green
                $ClaudeArgs = @("--model", $m.id) + $ClaudeArgs
            } else {
                Write-Host "Select a model:" -ForegroundColor Yellow
                for ($i = 0; $i -lt $modelsResp.Count; $i++) {
                    $m = $modelsResp[$i]
                    $hostLabel = if ($m.is_local) { "Ellie" } else { "Sparky" }
                    $displayName = if ($m.alias) { $m.alias } else { $m.id }
                    Write-Host "  $($i+1)) $displayName ($hostLabel)"
                }
                $sel = Read-Host "Enter number"
                if ($sel -match '^\d+$' -and [int]$sel -ge 1 -and [int]$sel -le $modelsResp.Count) {
                    $selected = $modelsResp[[int]$sel - 1]
                    $ClaudeArgs = @("--model", $selected.id) + $ClaudeArgs
                }
            }
        }
    }
    catch {
        Write-Host "Warning: Could not fetch models list" -ForegroundColor Yellow
    }
}

& claude @ClaudeArgs
