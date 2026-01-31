@echo off
setlocal enabledelayedexpansion

REM Wrapper for Claude Code CLI to use Local AI Stack from Windows
REM
REM NOTE: For login functionality, use claude-local.ps1 (PowerShell version)
REM       PowerShell: .\claude-local.ps1 -Login
REM
REM Usage: claude-local [args]
REM        claude-local --set-key <api-key>     - Set API key
REM        claude-local --set-url <base-url>    - Set base URL
REM        claude-local --config                - Show current config
REM        claude-local --reset-config          - Reset configuration

REM Configuration directory
set CONFIG_DIR=%USERPROFILE%\.config\claude-local
set CONFIG_FILE=%CONFIG_DIR%\config.bat

REM Default values
set DEFAULT_BASE_URL=http://10.20.10.5:8081

REM Load saved configuration if exists
if exist "%CONFIG_FILE%" (
    call "%CONFIG_FILE%"
)

REM Handle configuration commands
if "%~1"=="--set-key" (
    if "%~2"=="" (
        echo Error: API key required
        echo Usage: %~nx0 --set-key ^<api-key^>
        exit /b 1
    )
    if not exist "%CONFIG_DIR%" mkdir "%CONFIG_DIR%"
    echo set LLAMA_API_KEY=%~2> "%CONFIG_FILE%"
    if defined ANTHROPIC_BASE_URL echo set ANTHROPIC_BASE_URL=!ANTHROPIC_BASE_URL!>> "%CONFIG_FILE%"
    echo API key saved to %CONFIG_FILE%
    exit /b 0
)

if "%~1"=="--set-url" (
    if "%~2"=="" (
        echo Error: Base URL required
        echo Usage: %~nx0 --set-url ^<base-url^>
        echo Example: %~nx0 --set-url http://10.20.10.5:8081
        exit /b 1
    )
    if not exist "%CONFIG_DIR%" mkdir "%CONFIG_DIR%"
    if defined LLAMA_API_KEY (
        echo set LLAMA_API_KEY=!LLAMA_API_KEY!> "%CONFIG_FILE%"
    )
    echo set ANTHROPIC_BASE_URL=%~2>> "%CONFIG_FILE%"
    echo Base URL saved to %CONFIG_FILE%
    exit /b 0
)

if "%~1"=="--config" (
    echo Current configuration ^(%CONFIG_FILE%^):
    if defined LLAMA_API_KEY (
        echo   API Key: ********** ^(set^)
    ) else (
        echo   API Key: ^(not set^)
    )
    if defined ANTHROPIC_BASE_URL (
        echo   Base URL: !ANTHROPIC_BASE_URL!
    ) else (
        echo   Base URL: %DEFAULT_BASE_URL% ^(default^)
    )
    exit /b 0
)

if "%~1"=="--reset-config" (
    if exist "%CONFIG_FILE%" (
        del "%CONFIG_FILE%"
        echo Configuration reset. File removed: %CONFIG_FILE%
    ) else (
        echo No configuration file found.
    )
    exit /b 0
)

REM Check for API key
if not defined LLAMA_API_KEY (
    echo Error: API key not configured.
    echo.
    echo Set it using: %~nx0 --set-key ^<your-api-key^>
    echo.
    exit /b 1
)

REM Set base URL (use configured or default)
if not defined ANTHROPIC_BASE_URL (
    set ANTHROPIC_BASE_URL=%DEFAULT_BASE_URL%
)

REM Set environment variables for Claude
set ANTHROPIC_API_KEY=%LLAMA_API_KEY%
set CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1

REM Forward arguments to the real claude command
claude %*
