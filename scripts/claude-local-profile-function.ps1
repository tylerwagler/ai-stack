# Claude Local PowerShell Profile Function
#
# INSTALLATION:
# 1. Open your PowerShell profile:
#    notepad $PROFILE
#    (or: code $PROFILE)
#
# 2. Copy everything below this line and paste it at the end of your profile
#
# 3. Reload your profile:
#    . $PROFILE
#    (or restart PowerShell)
#
# ============================================================================

# Claude Local function - call from any directory
function claude-local {
    $scriptPath = "$env:USERPROFILE\.local\bin\claude-local.ps1"

    if (-not (Test-Path $scriptPath)) {
        Write-Host "Error: claude-local.ps1 not found at $scriptPath" -ForegroundColor Red
        Write-Host "Expected location: $scriptPath" -ForegroundColor Yellow
        return
    }

    # Capture all arguments explicitly
    # This ensures proper splatting to the script
    $scriptArgs = $args

    # Execute the script with all arguments
    & $scriptPath @scriptArgs
}

# Tab completion for claude-local commands and flags
Register-ArgumentCompleter -CommandName claude-local -ScriptBlock {
    param($commandName, $parameterName, $wordToComplete, $commandAst, $fakeBoundParameters)

    $completions = @(
        '-Login'
        '-SetKey'
        '-SetUrl'
        '-Config'
        '-ResetConfig'
        '--model'
        '--help'
        '--version'
    )

    $completions | Where-Object { $_ -like "$wordToComplete*" } | ForEach-Object {
        [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
    }
}

# Optional: Shorter alias
# Uncomment the line below if you want to use 'cl' instead of 'claude-local'
# Set-Alias -Name cl -Value claude-local

Write-Host "✓ Claude Local loaded" -ForegroundColor Green
