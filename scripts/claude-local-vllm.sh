#!/bin/bash
# Launch Claude Code against local vLLM backend
# Usage: ./claude-local-vllm.sh [model] [base_url]
#   model defaults to "Qwen3.5 35B"
#   base_url defaults to http://localhost:8012

DEFAULT_MODEL="Qwen3 Coder Next"
DEFAULT_BASE_URL="http://localhost:8012"

export ANTHROPIC_BASE_URL="${2:-$DEFAULT_BASE_URL}"
export ANTHROPIC_AUTH_TOKEN="none"
export ANTHROPIC_MODEL="${1:-$DEFAULT_MODEL}"
export ANTHROPIC_SMALL_FAST_MODEL="${1:-$DEFAULT_MODEL}"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="${1:-$DEFAULT_MODEL}"
export ANTHROPIC_DEFAULT_SONNET_MODEL="${1:-$DEFAULT_MODEL}"
export ANTHROPIC_DEFAULT_OPUS_MODEL="${1:-$DEFAULT_MODEL}"
export CLAUDE_CODE_ATTRIBUTION_HEADER=0

claude "$@"
