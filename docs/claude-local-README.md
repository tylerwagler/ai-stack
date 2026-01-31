# Claude Local Scripts - Usage Guide

Wrapper scripts to use Claude Code CLI with your local AI Stack instead of Anthropic's cloud API.

## Quick Start

### Linux/macOS (`claude-local`)

```bash
# RECOMMENDED: Login to retrieve your API key automatically
./claude-local --login

# Alternative: Set your API key manually
./claude-local --set-key sk-ant-your-api-key-here

# Optional: set custom base URL (auto-detected by default)
./claude-local --set-url http://10.20.10.5:8081

# Use Claude Code with your local stack
./claude-local "Explain this code"
./claude-local --model nemotron "Write a function"
```

### Windows PowerShell (`claude-local.ps1`)

```powershell
# RECOMMENDED: Login to retrieve your API key automatically
.\claude-local.ps1 -Login

# Alternative: Set your API key manually
.\claude-local.ps1 -SetKey sk-ant-your-api-key-here

# Optional: set custom base URL
.\claude-local.ps1 -SetUrl http://10.20.10.5:8081

# Use Claude Code with your local stack
.\claude-local.ps1 "Explain this code"
.\claude-local.ps1 -ClaudeArgs "--model","nemotron","Write a function"
```

### Windows Batch (`claude-local.bat`)

**Note:** The batch file version does not support login. Use PowerShell for login functionality.

```cmd
REM Set your API key manually
claude-local --set-key sk-ant-your-api-key-here

REM Optional: set custom base URL
claude-local --set-url http://10.20.10.5:8081

REM Use Claude Code with your local stack
claude-local "Explain this code"
```

## Configuration Commands

### Login and Retrieve API Key (Recommended)

The `--login` command authenticates with your AI Stack account and automatically retrieves or creates an API key for you.

```bash
# Linux/macOS
./claude-local --login

# Windows PowerShell
.\claude-local.ps1 -Login
```

**What happens during login:**
1. Prompts for your email and password
2. Authenticates with your AI Stack (Supabase)
3. Lists any existing API keys you've created
4. Lets you select an existing key OR create a new one
5. Automatically saves the key to your config file

**Interactive flow example:**
```
$ ./claude-local --login
Login to AI Stack

Email: your@email.com
Password: ********
Authenticating with AI Stack...
Authentication successful! Retrieving API keys...

Found 2 existing API key(s):
  1. claude-local-20260125-143022 (created: 2026-01-25)
  2. my-api-key (created: 2026-01-20)

Select a key to use (1-2), or press Enter to create a new one: 1
Using existing API key.

✓ API key configured successfully!
  Config saved to: /home/user/.config/claude-local/env

You can now use: ./claude-local "Your prompt"
```

**Benefits:**
- No need to manually copy/paste API keys
- See all your existing keys in one place
- Automatically generates secure keys
- Links your local CLI to your AI Stack account with usage tracking

### Set API Key Manually

If you already have an API key, you can set it directly:

```bash
# Linux/macOS
./claude-local --set-key sk-ant-your-key-here

# Windows PowerShell
.\claude-local.ps1 -SetKey sk-ant-your-key-here

# Windows Batch
claude-local --set-key sk-ant-your-key-here
```

### Set Base URL

```bash
# Linux/macOS
./claude-local --set-url http://10.20.10.5:8081

# Windows
claude-local --set-url http://10.20.10.5:8081
```

### View Configuration

```bash
# Linux/macOS
./claude-local --config

# Windows
claude-local --config
```

Example output:
```
Current configuration (/home/user/.config/claude-local/env):
  API Key: ********** (set)
  Base URL: http://10.20.10.5:8081
  AI Stack Host: ellie
  AI Stack IP: 10.20.10.5
```

### Reset Configuration

```bash
# Linux/macOS
./claude-local --reset-config

# Windows
claude-local --reset-config
```

## Configuration Storage

### Linux/macOS
- Config file: `~/.config/claude-local/env`
- Automatically loads from:
  1. Saved configuration file
  2. Local `.env` files (for development)
  3. Prompts if not found

### Windows
- Config file: `%USERPROFILE%\.config\claude-local\config.bat`
- Prompts for API key on first use if not configured

## Advanced Features (Linux/macOS only)

### Set AI Stack Host/IP

```bash
# Set hostname for local detection
./claude-local --set-host ellie

# Set IP address for remote access
./claude-local --set-ip 10.20.10.5
```

### Auto-Detection

The Linux script automatically detects the best base URL:
- If running on the AI stack host (`ellie`): uses `http://localhost:8081`
- If the configured IP is pingable: uses `http://10.20.10.5:8081`
- Otherwise: uses hostname.local (`http://ellie.local:8081`)

You can override this by setting a custom URL with `--set-url`.

### Interactive Model Selection

If you don't specify `--model`, the script will:
1. Fetch available models from your AI stack
2. Present an interactive menu
3. Let you select which model to use

## Examples

### Basic Usage

```bash
# Use with auto-detected settings
./claude-local "What does this function do?"

# Specify a model
./claude-local --model nemotron "Optimize this code"

# Long-form conversation
./claude-local
```

### Configuration Workflow

```bash
# Check current config
./claude-local --config

# Update API key
./claude-local --set-key sk-ant-new-key

# Switch to different AI stack
./claude-local --set-url http://192.168.1.100:8081

# Verify changes
./claude-local --config

# Test connection
./claude-local "test"
```

### Development Setup

```bash
# Option 1: Use saved config (recommended for end users)
./claude-local --set-key sk-ant-your-key
./claude-local "Hello"

# Option 2: Use local .env file (for developers)
# Create .env in the same directory
echo "LLAMA_API_KEY=sk-ant-your-key" > .env
./claude-local "Hello"
```

## Troubleshooting

### "API key not configured" error

**Solution**: Login to retrieve your API key or set it manually
```bash
# Recommended: Login to your account
./claude-local --login

# Or set manually
./claude-local --set-key sk-ant-your-api-key
```

### Login fails with "Authentication failed"

**Possible causes**:
1. Incorrect email or password
2. AI Stack is not running
3. Supabase authentication service is down
4. Network connectivity issue

**Solutions**:
```bash
# Verify AI Stack is running
curl http://10.20.10.5:8081/health

# Check if Supabase is accessible
curl http://10.20.10.5:8081/auth/v1/health

# Try manual key setup as fallback
./claude-local --set-key <your-existing-key>
```

### Login succeeds but no API keys found

If you're a new user with no existing keys, the script will automatically create one for you. Just press Enter when prompted.

### "Could not connect to AI Stack" error

**Possible causes**:
1. AI stack is not running
2. Wrong base URL configured
3. Network connectivity issue

**Solutions**:
```bash
# Check current URL
./claude-local --config

# Update URL if incorrect
./claude-local --set-url http://correct-ip:8081

# Test connectivity
curl http://10.20.10.5:8081/v1/models -H "Authorization: Bearer sk-ant-your-key"
```

### Model selection not working

The script requires Python 3 and `curl` for model fetching. If unavailable, specify the model manually:
```bash
./claude-local --model nemotron "Your prompt"
```

## Environment Variables

These are set automatically by the scripts:

- `ANTHROPIC_API_KEY`: Your API key (copied from LLAMA_API_KEY)
- `ANTHROPIC_BASE_URL`: Base URL for API requests
- `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`: Disables telemetry (set to 1)

## Network and Firewall Requirements

### Ports Required

The login feature uses **port 3000** (temper-view) for authentication, which is separate from port 8081 (llama-proxy) used for LLM inference.

**Firewall rules needed:**
```bash
# For LLM inference (always required)
Port 8081 - llama-proxy (OpenAI-compatible API)

# For login feature (required if using --login)
Port 3000 - temper-view (Supabase auth + REST API)
```

### Architecture

```
Login Flow:
  claude-local --login → Port 3000 (temper-view Nginx)
                           ├→ /auth/v1/* → Supabase Auth
                           └→ /rest/v1/* → PostgreSQL (API keys)

Inference Flow:
  claude-local "prompt" → Port 8081 (llama-proxy) → Port 8082 (llama-server)
```

**Important**: Both ports route through different services:
- **Port 3000**: temper-view with Nginx reverse proxy to Supabase
- **Port 8081**: llama-proxy for LLM requests only

If you **only have port 8081 open**, you can still use the manual setup:
```bash
./claude-local --set-key sk-ant-your-key-here
```

## Security Notes

- Configuration files are created with restricted permissions (600 on Linux)
- API keys are stored in plaintext in config files
- Config directory: `~/.config/claude-local/` (Linux) or `%USERPROFILE%\.config\claude-local\` (Windows)
- Keep your config files secure and don't commit them to git

## Integration with Claude Code

These scripts are drop-in replacements for the `claude` command:

```bash
# Instead of:
claude "Your prompt"

# Use:
./claude-local "Your prompt"
```

All Claude Code CLI flags and features work normally:
- `--model`: Select model
- `--help`: Show help
- `--version`: Show version
- Multi-turn conversations
- File operations
- Tool usage
