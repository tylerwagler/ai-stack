#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="docker-compose-sparky.yml"
ENV_FILE=".env"

cd "$PROJECT_DIR"

# Ensure docker context exists
if ! docker context inspect sparky &>/dev/null; then
    echo "Creating docker context 'sparky'..."
    docker context create sparky --docker "host=ssh://tyler@sparky"
fi

export DOCKER_CONTEXT=sparky

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
docker compose \
    -f "$COMPOSE_FILE" \
    --env-file "$ENV_FILE" \
    up -d $BUILD_FLAG --remove-orphans "${SERVICES[@]+"${SERVICES[@]}"}"

echo ""
echo "Checking service status..."
docker compose \
    -f "$COMPOSE_FILE" \
    ps
