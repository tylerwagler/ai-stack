# Claude Local Scripts

Wrapper scripts to use Claude Code CLI with your local AI stack (Open WebUI + LiteLLM) instead of Anthropic's cloud API.

## Architecture

All traffic goes through a single gateway on port 3000:

```
claude-local → gateway (:3000)
                 ├─ /v1/*  → auth_request → LiteLLM (Anthropic API)
                 └─ /*     → Open WebUI (web interface)
```

- **Authentication**: Open WebUI manages users and API keys (`sk-*`)
- **Inference**: LiteLLM serves both Anthropic (`/v1/messages`) and OpenAI (`/v1/chat/completions`) formats
- **Gateway**: nginx validates your `sk-*` key against Open WebUI, then proxies to LiteLLM with the master key

## Quick Start

### Linux/macOS

```bash
# Login to get your API key
./scripts/claude-local --login

# Launch Claude Code
./scripts/claude-local

# Launch with a specific model
./scripts/claude-local --model "GLM 4.7 Flash"
```

### Windows PowerShell

```powershell
# Login to get your API key
.\scripts\claude-local.ps1 -Login

# Launch Claude Code
.\scripts\claude-local.ps1

# Launch with a specific model
.\scripts\claude-local.ps1 -ClaudeArgs "--model","GLM 4.7 Flash"
```

## Commands

| Command | Bash | PowerShell |
|---------|------|------------|
| Login | `--login` | `-Login` |
| Set key manually | `--set-key <key>` | `-SetKey <key>` |
| Set URL | `--set-url <url>` | `-SetUrl <url>` |
| Show config | `--config` | `-Config` |
| Reset config | `--reset-config` | `-ResetConfig` |
| Version | `--version` | `-Version` |

## Login Flow

```
$ ./scripts/claude-local --login
Login to AI Stack

Email: your@email.com
Password: ********
Authenticating with Open WebUI...
Authentication successful! Checking API key...
Found existing API key.

API key configured successfully!
  Config saved to: /home/user/.config/claude-local/env

You can now run: ./scripts/claude-local
```

**What happens:**
1. Signs in to Open WebUI with your email/password
2. Checks for an existing API key on your account
3. Creates one if none exists
4. Saves the `sk-*` key to `~/.config/claude-local/env`

## Model Selection

If you don't specify `--model`, the script fetches available models from LiteLLM and prompts you to choose. Models are filtered to exclude non-chat models:
- Embedding models (e.g. `Qwen3-Embedding-0.6B`)
- Reranking models (e.g. `Qwen3-Reranker-0.6B`)
- RAG pipeline models (e.g. `GLM 4.7 Flash (RAG)`)

## Environment Variables

The scripts set these before launching Claude Code:

| Variable | Value | Purpose |
|----------|-------|---------|
| `ANTHROPIC_API_KEY` | Your `sk-*` key | Auth for Claude Code |
| `ANTHROPIC_BASE_URL` | `http://<host>:3000/v1` | Points Claude Code at the gateway |
| `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` | `1` | Prevents telemetry to Anthropic |

## Configuration

Config is stored in `~/.config/claude-local/env` (both platforms) with format:
```
CLAUDE_LOCAL_API_KEY=sk-...
CLAUDE_LOCAL_URL=http://10.20.10.5:3000
```

The default URL is `http://10.20.10.5:3000`. Override with `--set-url`.

## Troubleshooting

### "Authentication failed"

- Check email and password
- Ensure Open WebUI is running: open `http://<host>:3000` in a browser
- New user? Sign up at `http://<host>:3000` first

### "API key validation failed"

- Key may have been revoked in Open WebUI settings
- Re-login: `./scripts/claude-local --login`

### "Could not validate API key"

- Gateway may be down: `curl http://<host>:3000/v1/models -H "x-api-key: sk-..."`
- Check docker status: `docker compose ps gateway`

### Model selection empty

- LiteLLM may still be starting: `docker compose logs litellm`
- Test directly: `curl http://<host>:3000/v1/models -H "x-api-key: sk-..."`
