# AGENTS.md — Instructions for Agentic Coding

## Quick Start

- **System**: GPU-accelerated AI inference platform with Open WebUI, model routing, and RAG
- **Primary service**: ai-proxy (model routing) — Python code in `/home/tyler/ai-stack/ai-proxy/`
- **Build**: `docker compose up -d --build <service>`
- **Test ai-proxy**: `cd ai-proxy && pytest tests/ -v`

---

## Project Structure

| Directory | Purpose |
|-----------|---------|
| `ai-proxy/` | Model routing proxy (main Python service) |
| `gateway/` | nginx reverse proxy + auth |
| `fan-control/` | GPU fan/power controller (Ellie host) |
| `vl-embed/` | vLLM embedding/reranking proxy |
| `scripts/` | Deployment and utility scripts |
| `temp/` | **ALL TEMP/WORKING FILES** (gitignored) |

---

## Python Code Style

### Imports
- Standard library first (`sys`, `os`, `json`, `http.server`)
- Third-party (`requests`, `fastapi`, `pynvml`, `psutil`)
- Local project imports last
- Use absolute imports within `ai-proxy/`

### Naming Conventions
- **Functions/variables**: `snake_case` (e.g., `get_available_models`, `fan_curve`)
- **Classes**: `PascalCase` (e.g., `GPUController`, `ProxyHandler`)
- **Constants**: `UPPER_SNAKE_CASE` (e.g., `POLL_INTERVAL`, `DEFAULT_BACKEND`)

### Type Hints
- Use Python type hints for function signatures (e.g., `def parse_curve(raw: str) -> list:`)
- For complex types, use `List`, `Dict`, `Optional`, `Union` from `typing`

### Error Handling
- Log errors to `sys.stderr` with context
- Return `None` or empty list for recoverable failures (e.g., network timeouts)
- Exit with code 1 for critical startup errors (e.g., missing config)

### Formatting
- Use 4-space indentation
- Max line length: 125 characters (follow existing style)
- Triple-quoted docstrings for public functions

---

## Testing (ai-proxy)

### Dependencies
```bash
cd ai-proxy
pip install -r requirements-dev.txt
```

### Run All Tests
```bash
pytest tests/ -v
```

### Run Single Test
```bash
pytest tests/test_auth.py::TestAuthentication::test_system_key_bypass -v
pytest tests/test_model_routing.py -k "test_get_available_models_success" -v
```

### Fixtures (see `tests/conftest.py`)
- `mock_db_conn`, `mock_cursor`: Mock database
- `mock_api_key_data`: Valid API key data structure
- `mock_env_vars`: Set test environment variables
- `clear_caches`: Auto-clears `KEY_CACHE` and `LOADED_MODEL_CACHE` before each test

---

## Shell Scripts

- **Shebang**: `#!/usr/bin/env bash`
- **Errors**: Use `set -e` at top
- **Parameters**: Use `$1`, `$2`, `$3` etc.
- **Logging**: `echo` to stdout, errors to stderr

### Key Scripts
| Script | Purpose |
|--------|---------|
| `scripts/deploy-sparky.sh` | Deploy Sparky host |
| `scripts/rotate-logs.sh` | Log rotation utility |
| `gateway/install/setup.sh` | claude-local setup |

---

## Docker & Deployment

### Common Commands
```bash
docker compose up -d                    # Start all services
docker compose up -d --build ai-proxy   # Rebuild specific service
docker compose logs -f ai-proxy         # View logs
docker compose restart ai-proxy         # Restart service
```

### Test Endpoints
```bash
# Gateway (with auth)
curl http://localhost:3000/v1/models -H "x-api-key: sk-..."

# ai-proxy directly
curl -X POST http://localhost:8081/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"GLM 4.7 Flash","messages":[{"role":"user","content":"Hello"}]}'
```

---

## Environment Variables

| Variable | Purpose | Location |
|----------|---------|----------|
| `PROXY_PORT` | ai-proxy listening port (default: 8081) | ai-proxy |
| `MODELS_CONFIG` | Path to `models.json` (default: /app/models.json) | ai-proxy |
| `FAN_SETPOINTS` | Fan curve: `temp:speed temp:speed ...` | fan-control |
| `POWER_SETPOINTS` | Power limit curve: `temp:watts ...` | fan-control |

---

## Sub-Agent Load Distribution

When spawning sub-agents:
- Use `model: "haiku"` for Explore, Bash, and quick research tasks
- Routes to Ellie (GLM 4.7 Flash) backend
- Main agent runs on Sparky (Qwen3 Coder)

---

## Build/Lint/Test Commands

```bash
# Validate ai-proxy syntax
cd ai-proxy && python -m py_compile proxy.py

# Run ai-proxy tests
cd ai-proxy && pytest tests/ -v

# Check Python formatting (if using black/prettier)
# Currently: no formal formatter enforced; follow existing style

# Validate shell scripts
shellcheck scripts/*.sh

# Build and test ai-proxy
cd ai-proxy && docker build -t ai-proxy:test . && docker compose up -d --build ai-proxy
```

---

## Working Files Policy

**ALL temporary and working files MUST go in `/temp` directory.**
- Test scripts, debug output, scratch files → `/temp`
- The `/temp` directory is gitignored
- Do NOT create files in the repository root

---

## Key Files

| File | Purpose |
|------|---------|
| `ai-proxy/models.json` | Model routing config (names, URLs, aliases) |
| `ai-proxy/proxy.py` | Routing proxy — passes through Anthropic + OpenAI formats |
| `gateway/nginx.conf.template` | Auth subrequest + proxy routing |
| `fan-control/main.py` | GPU fan/power control (pynvml) |
| `vl-embed/server.py` | vLLM reranking proxy (FastAPI) |

---

##常见问题

### Gateway returns 401
- Verify your `sk-*` key exists in Open WebUI (Settings > API Keys)
- Test: `curl http://localhost:3000/v1/models -H "x-api-key: sk-..."`

### Model not found
- Check `ai-proxy/models.json` for correct model names and aliases
- Restart ai-proxy: `docker compose restart ai-proxy`

### fan-control not working
- Requires `privileged: true` in Docker
- Check logs: `docker logs fan-control`
- Verify `FAN_SETPOINTS` format: `50:30 70:65 78:95 80:100`
