@echo off
REM Wrapper for Claude Code CLI to use Local AI Stack from Windows
REM Usage: claude-local --model nemotron "Your prompt"

REM Set the base URL to your local AI stack (IP provided by user)
set ANTHROPIC_BASE_URL=http://10.20.10.5:8081
REM Disable telemetry, auto-updates, and error reporting
set CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1

REM Forward arguments to the real claude command
call claude %*
