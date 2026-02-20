#!/bin/bash
# setup.sh — Install claude-local on Linux/macOS
#
# Usage:
#   curl -fsSL https://your-server/install/setup.sh | bash
#
# The server URL is auto-injected by the gateway when served over HTTP.
# You can also override it with an argument or environment variable:
#   curl ... | bash -s -- https://custom-url
#   CLAUDE_LOCAL_URL=https://custom-url bash setup.sh

set -e

# __SERVER_URL__ is replaced by the gateway's nginx sub_filter with the
# actual scheme://host the user curled from. If running the script locally
# (not via the gateway), it stays as the literal placeholder.
INJECTED_URL="__SERVER_URL__"

# Guard: build the placeholder at runtime so sub_filter can't replace it
PLACEHOLDER="__SERVER""_URL__"

# Priority: CLI arg > env var > injected URL
SERVER_URL="${1:-${CLAUDE_LOCAL_URL:-}}"
if [ -z "$SERVER_URL" ]; then
    if [ "$INJECTED_URL" != "$PLACEHOLDER" ]; then
        SERVER_URL="$INJECTED_URL"
    fi
fi

INSTALL_DIR="$HOME/.local/bin"
SCRIPT_NAME="claude-local"

echo "=== Claude Local Installer ==="
echo ""

if [ -z "$SERVER_URL" ]; then
    echo "Error: Could not determine server URL."
    echo ""
    echo "Usage: curl -fsSL https://your-server/install/setup.sh | bash"
    exit 1
fi

# Strip trailing slash
SERVER_URL="${SERVER_URL%/}"

echo "Server: $SERVER_URL"
echo "Install to: $INSTALL_DIR/$SCRIPT_NAME"
echo ""

# 1. Check if claude CLI is installed
if ! command -v claude &>/dev/null; then
    echo "Claude Code CLI not found. Installing..."
    curl -fsSL https://claude.ai/install.sh | bash
    echo ""
fi

# 2. Create install directory
mkdir -p "$INSTALL_DIR"

# 3. Download claude-local script
echo "Downloading claude-local..."
curl -fsSL "$SERVER_URL/install/claude-local" -o "$INSTALL_DIR/$SCRIPT_NAME"
chmod +x "$INSTALL_DIR/$SCRIPT_NAME"

# 4. Write/update config with server URL (preserves existing API key)
CONFIG_DIR="$HOME/.config/claude-local"
CONFIG_FILE="$CONFIG_DIR/env"
mkdir -p "$CONFIG_DIR"
# Read existing API key if config exists
EXISTING_KEY=""
if [ -f "$CONFIG_FILE" ]; then
    while IFS='=' read -r key value; do
        [ "$key" = "CLAUDE_LOCAL_API_KEY" ] && EXISTING_KEY="$value"
    done < "$CONFIG_FILE"
fi
{
    echo "CLAUDE_LOCAL_URL=$SERVER_URL"
    [ -n "$EXISTING_KEY" ] && echo "CLAUDE_LOCAL_API_KEY=$EXISTING_KEY"
} > "$CONFIG_FILE"
chmod 600 "$CONFIG_FILE"
echo "Config written to $CONFIG_FILE"

# 5. Ensure ~/.local/bin is in PATH
add_to_path() {
    local rc_file="$1"
    if [ -f "$rc_file" ]; then
        if ! grep -q '\.local/bin' "$rc_file" 2>/dev/null; then
            echo '' >> "$rc_file"
            echo '# Added by claude-local installer' >> "$rc_file"
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$rc_file"
            echo "Added ~/.local/bin to PATH in $rc_file"
            return 0
        fi
    fi
    return 1
}

if ! echo "$PATH" | tr ':' '\n' | grep -q "$HOME/.local/bin"; then
    added=false
    for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
        if [ -f "$rc" ]; then
            add_to_path "$rc" && added=true
        fi
    done
    if [ "$added" = false ]; then
        add_to_path "$HOME/.profile"
    fi
    export PATH="$HOME/.local/bin:$PATH"
fi

echo ""
echo "Installation complete!"
echo ""
echo "Get started:"
echo "  claude-local --login    # Authenticate with your account"
echo "  claude-local            # Start Claude Code with local inference"
echo ""
echo "If 'claude-local' is not found, restart your shell or run:"
echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
