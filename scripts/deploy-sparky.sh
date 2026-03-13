#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# Ensure docker context exists
if ! docker context inspect sparky &>/dev/null; then
    echo "Creating docker context 'sparky'..."
    docker context create sparky --docker "host=ssh://tyler@sparky"
fi

export DOCKER_CONTEXT=sparky
export COMPOSE_PROFILES=sparky

# Parse args
SERVICES=()
BUILD_FLAG="--build"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-build) BUILD_FLAG=""; shift ;;
        *) SERVICES+=("$1"); shift ;;
    esac
done

echo "Deploying to Sparky via docker context..."
docker compose up -d $BUILD_FLAG --remove-orphans "${SERVICES[@]+"${SERVICES[@]}"}"

echo ""
echo "Checking service status..."
docker compose ps
