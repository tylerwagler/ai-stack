# PowerShell wrapper for Claude Code CLI to use Local AI Stack
# Usage: .\claude-local.ps1 [args]
#        .\claude-local.ps1 -Login                  # Login and retrieve/create API key
#        .\claude-local.ps1 -SetKey <api-key>       # Set API key manually
#        .\claude-local.ps1 -SetUrl <base-url>      # Set base URL
#        .\claude-local.ps1 -Config                 # Show current config
#        .\claude-local.ps1 -ResetConfig            # Reset configuration

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

    $configContent = @()
    if ($script:ApiKey) {
        $configContent += "`$env:LLAMA_API_KEY = `"$script:ApiKey`""
    }
    if ($script:BaseUrl) {
        $configContent += "`$env:ANTHROPIC_BASE_URL = `"$script:BaseUrl`""
    }

    $configContent -join "`n" | Out-File -FilePath $ConfigFile -Encoding UTF8
}

# Helper function to generate secure API key
function New-ApiKey {
    $randomBytes = New-Object byte[] 16
    [Security.Cryptography.RNGCryptoServiceProvider]::Create().GetBytes($randomBytes)
    $randomPart = ([BitConverter]::ToString($randomBytes) -replace '-','').Substring(0,30).ToLower()
    return "sk_ai_$randomPart"
}

# Helper function to authenticate and retrieve/create API key
function Get-ApiKeyFromLogin {
    param(
        [string]$Email,
        [securestring]$Password
    )

    # Supabase is exposed via temper-view on port 3000, NOT ai-proxy on 8081
    # The Nginx reverse proxy routes /auth/v1/ and /rest/v1/ to Supabase Kong
    $supabaseUrl = if ($env:SUPABASE_URL) {
        $env:SUPABASE_URL
    } else {
        "http://10.20.10.5:3000"
    }

    # Supabase anon key (from temper-view)
    $anonKey = "eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9.eyJyb2xlIjogImFub24iLCAiaXNzIjogInN1cGFiYXNlIiwgImlhdCI6IDE3Njk1MjYyNTgsICJleHAiOiAyMDg0ODg2MjU4fQ.UYjtcXHj4l-9rEHMzNk2rqc-djmaPTtumgylFpG5NfA"

    Write-Host "Authenticating with AI Stack..."

    # Convert SecureString to plain text for API call
    $BSTR = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($Password)
    $plainPassword = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR)

    # Authenticate with Supabase
    $authBody = @{
        email = $Email
        password = $plainPassword
    } | ConvertTo-Json

    try {
        $authResponse = Invoke-RestMethod -Uri "$supabaseUrl/auth/v1/token?grant_type=password" `
            -Method Post `
            -Headers @{
                "apikey" = $anonKey
                "Content-Type" = "application/json"
            } `
            -Body $authBody `
            -ErrorAction Stop
    }
    catch {
        Write-Host "Error: Authentication failed - $($_.Exception.Message)" -ForegroundColor Red
        return $null
    }

    $accessToken = $authResponse.access_token
    $userId = $authResponse.user.id

    if (-not $accessToken) {
        Write-Host "Error: Failed to authenticate" -ForegroundColor Red
        return $null
    }

    Write-Host "Authentication successful! Retrieving API keys..." -ForegroundColor Green

    # Fetch existing API keys
    try {
        $keysResponse = Invoke-RestMethod -Uri "$supabaseUrl/rest/v1/api_keys?select=api_key,name,created_at&order=created_at.desc" `
            -Method Get `
            -Headers @{
                "apikey" = $anonKey
                "Authorization" = "Bearer $accessToken"
                "Content-Type" = "application/json"
            } `
            -ErrorAction Stop
    }
    catch {
        Write-Host "Error: Failed to fetch API keys - $($_.Exception.Message)" -ForegroundColor Red
        return $null
    }

    $keyCount = $keysResponse.Count

    if ($keyCount -gt 0) {
        Write-Host "`nFound $keyCount existing API key(s):" -ForegroundColor Cyan
        for ($i = 0; $i -lt $keyCount; $i++) {
            $key = $keysResponse[$i]
            $num = $i + 1
            $name = $key.name
            # PowerShell parses JSON dates as DateTime objects, so format them
            if ($key.created_at -is [DateTime]) {
                $created = $key.created_at.ToString("yyyy-MM-dd")
            } else {
                $created = $key.created_at.ToString().Substring(0,10)
            }
            Write-Host "  $num. $name (created: $created)"
        }

        Write-Host "`nSelect a key to use (1-$keyCount), or press Enter to create a new one: " -NoNewline
        $selection = Read-Host

        if ($selection -match '^\d+$' -and [int]$selection -ge 1 -and [int]$selection -le $keyCount) {
            $selectedKey = $keysResponse[[int]$selection - 1].api_key
            Write-Host "Using existing API key." -ForegroundColor Green
            return $selectedKey
        }
    }

    # Create new API key
    Write-Host "Creating new API key..." -ForegroundColor Yellow
    $newKey = New-ApiKey
    $keyName = "claude-local-$(Get-Date -Format 'yyyyMMdd-HHmmss')"

    $createBody = @{
        api_key = $newKey
        name = $keyName
        user_id = $userId
    } | ConvertTo-Json

    try {
        $createResponse = Invoke-RestMethod -Uri "$supabaseUrl/rest/v1/api_keys" `
            -Method Post `
            -Headers @{
                "apikey" = $anonKey
                "Authorization" = "Bearer $accessToken"
                "Content-Type" = "application/json"
                "Prefer" = "return=representation"
            } `
            -Body $createBody `
            -ErrorAction Stop

        Write-Host "Successfully created new API key: $keyName" -ForegroundColor Green
        return $createResponse[0].api_key
    }
    catch {
        Write-Host "Error: Failed to create API key - $($_.Exception.Message)" -ForegroundColor Red
        return $null
    }
}

# Load existing configuration
if (Test-Path $ConfigFile) {
    . $ConfigFile
    $script:ApiKey = $env:LLAMA_API_KEY
    $script:BaseUrl = $env:ANTHROPIC_BASE_URL
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
        Write-Host "  API Key: ********** (set)" -ForegroundColor Green
    } else {
        Write-Host "  API Key: (not set)" -ForegroundColor Yellow
    }
    if ($script:BaseUrl) {
        Write-Host "  Base URL: $script:BaseUrl"
    } else {
        Write-Host "  Base URL: $DefaultBaseUrl (default)"
    }
    exit 0
}

if ($ResetConfig) {
    if (Test-Path $ConfigFile) {
        Remove-Item $ConfigFile
        Write-Host "Configuration reset. File removed: $ConfigFile" -ForegroundColor Green
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

    $retrievedKey = Get-ApiKeyFromLogin -Email $email -Password $password

    if ($retrievedKey) {
        $script:ApiKey = $retrievedKey
        Save-Config
        Write-Host ""
        Write-Host "✓ API key configured successfully!" -ForegroundColor Green
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
    Write-Host "No API key found. To use Claude Code with your local AI Stack," -ForegroundColor Yellow
    Write-Host "you'll need to authenticate and retrieve your API key." -ForegroundColor Yellow
    Write-Host ""

    $response = Read-Host "Would you like to log in now? (y/n)"

    if ($response -match '^[Yy]') {
        Write-Host ""
        Write-Host "Starting login process..." -ForegroundColor Cyan
        Write-Host ""

        $email = Read-Host "Email"
        if (-not $email) {
            Write-Host "Error: Email required" -ForegroundColor Red
            exit 1
        }

        $password = Read-Host "Password" -AsSecureString

        $retrievedKey = Get-ApiKeyFromLogin -Email $email -Password $password

        if ($retrievedKey) {
            $script:ApiKey = $retrievedKey
            Save-Config
            Write-Host ""
            Write-Host "✓ API key configured successfully!" -ForegroundColor Green
            Write-Host ""
            Write-Host "You're all set! Continuing with Claude Code..." -ForegroundColor Cyan
            Write-Host ""

            # Update environment for this session
            $env:ANTHROPIC_API_KEY = $script:ApiKey
            $env:LLAMA_API_KEY = $script:ApiKey
        }
        else {
            Write-Host ""
            Write-Host "Failed to retrieve API key." -ForegroundColor Red
            Write-Host ""
            Write-Host "You can try again or set a key manually:" -ForegroundColor Yellow
            Write-Host "  .\claude-local.ps1              # Try again"
            Write-Host "  .\claude-local.ps1 -SetKey <key>  # Set manually"
            Write-Host ""
            exit 1
        }
    }
    else {
        Write-Host ""
        Write-Host "No problem! You can authenticate later using:" -ForegroundColor Yellow
        Write-Host "  .\claude-local.ps1 -Login         # Login to get a key"
        Write-Host "  .\claude-local.ps1 -SetKey <key>  # Set a key manually"
        Write-Host ""
        exit 1
    }
}

# Set environment variables
$env:ANTHROPIC_API_KEY = $script:ApiKey
$env:LLAMA_API_KEY = $script:ApiKey
$env:ANTHROPIC_BASE_URL = if ($script:BaseUrl) { $script:BaseUrl } else { $DefaultBaseUrl }
$env:CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = "1"

# Validate API key before launching Claude
Write-Host "Validating API key..." -ForegroundColor Cyan

try {
    $validationResponse = Invoke-WebRequest -Uri "$env:ANTHROPIC_BASE_URL/v1/models" `
        -Headers @{ "Authorization" = "Bearer $script:ApiKey" } `
        -Method Get `
        -UseBasicParsing `
        -ErrorAction Stop

    Write-Host "✓ API key valid" -ForegroundColor Green
    Write-Host ""
}
catch {
    $statusCode = $_.Exception.Response.StatusCode.value__

    if ($statusCode -eq 401 -or $statusCode -eq 403) {
        Write-Host ""
        Write-Host "❌ API key validation failed: Invalid or expired key" -ForegroundColor Red
        Write-Host ""
        Write-Host "Your API key is not valid. This can happen if:" -ForegroundColor Yellow
        Write-Host "  • The key was deleted or revoked"
        Write-Host "  • The key expired"
        Write-Host "  • You're using an old key from a different setup"
        Write-Host ""

        # Prompt user to log in
        $response = Read-Host "Would you like to log in now to get a valid key? (y/n)"

        if ($response -match '^[Yy]') {
            Write-Host ""
            Write-Host "Starting login process..." -ForegroundColor Cyan
            Write-Host ""

            $email = Read-Host "Email"
            if (-not $email) {
                Write-Host "Error: Email required" -ForegroundColor Red
                exit 1
            }

            $password = Read-Host "Password" -AsSecureString

            $retrievedKey = Get-ApiKeyFromLogin -Email $email -Password $password

            if ($retrievedKey) {
                $script:ApiKey = $retrievedKey
                Save-Config
                Write-Host ""
                Write-Host "✓ API key configured successfully!" -ForegroundColor Green
                Write-Host "  Continuing with Claude Code..."
                Write-Host ""

                # Update environment for this session
                $env:ANTHROPIC_API_KEY = $script:ApiKey
                $env:LLAMA_API_KEY = $script:ApiKey
            }
            else {
                Write-Host ""
                Write-Host "Failed to retrieve API key." -ForegroundColor Red
                Write-Host ""
                Write-Host "You can also set a key manually:" -ForegroundColor Yellow
                Write-Host "  .\claude-local.ps1 -SetKey <your-key>"
                Write-Host ""
                exit 1
            }
        }
        else {
            Write-Host ""
            Write-Host "Alternatively, you can:" -ForegroundColor Yellow
            Write-Host "  .\claude-local.ps1 -Login          # Run login separately"
            Write-Host "  .\claude-local.ps1 -SetKey <key>  # Set a key manually"
            Write-Host "  .\claude-local.ps1 -Config         # Show current configuration"
            Write-Host ""
            exit 1
        }
    }
    else {
        Write-Host ""
        Write-Host "⚠️  Warning: Could not validate API key (HTTP $statusCode)" -ForegroundColor Yellow
        Write-Host "The AI Stack may not be reachable at: $env:ANTHROPIC_BASE_URL"
        Write-Host ""
        Write-Host "Continuing anyway... (use Ctrl+C to cancel)"
        Start-Sleep -Seconds 2
    }
}

# Check if user specified a model
$hasModel = $false
foreach ($arg in $ClaudeArgs) {
    if ($arg -eq "--model" -or $arg -eq "-m" -or $arg -eq "--help" -or $arg -eq "-h" -or $arg -eq "--version" -or $arg -eq "-v") {
        $hasModel = $true
        break
    }
}

# If no model specified, fetch available models and prompt
if (-not $hasModel) {
    Write-Host "Fetching available models from AI Stack..." -ForegroundColor Cyan

    try {
        $modelsResponse = Invoke-RestMethod -Uri "$env:ANTHROPIC_BASE_URL/v1/models" `
            -Headers @{ "Authorization" = "Bearer $env:LLAMA_API_KEY" } `
            -Method Get `
            -ErrorAction Stop

        if ($modelsResponse.data -and $modelsResponse.data.Count -gt 0) {
            $modelList = @()
            foreach ($model in $modelsResponse.data) {
                $modelList += $model.id
            }

            if ($modelList.Count -eq 1) {
                # Only one model available, auto-select it
                $selectedModel = $modelList[0]
                Write-Host "Using model: $selectedModel" -ForegroundColor Green
                $ClaudeArgs = @("--model", $selectedModel) + $ClaudeArgs
            }
            else {
                # Multiple models, let user choose
                Write-Host ""
                Write-Host "Select a model to use:" -ForegroundColor Yellow

                $i = 1
                foreach ($modelId in $modelList) {
                    Write-Host "  $i) $modelId"
                    $i++
                }

                Write-Host ""
                $selection = Read-Host "Enter number (or press Enter to skip)"

                if ($selection -match '^\d+$' -and [int]$selection -ge 1 -and [int]$selection -le $modelList.Count) {
                    $selectedModel = $modelList[[int]$selection - 1]
                    Write-Host "Using model: $selectedModel" -ForegroundColor Green
                    $ClaudeArgs = @("--model", $selectedModel) + $ClaudeArgs
                }
            }
        }
    }
    catch {
        Write-Host "Warning: Could not fetch models list" -ForegroundColor Yellow
    }
}

# Run Claude Code with arguments
& claude @ClaudeArgs
