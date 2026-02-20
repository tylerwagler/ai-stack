# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Working Files Policy

**All temporary and working files MUST go in the `/temp` directory.** This includes test scripts, debug output, scratch files, and anything not part of the permanent codebase. The `/temp` directory is gitignored.

**Do NOT create files in the repository root.**

## System Overview

GPU-accelerated AI inference platform with Open WebUI frontend, model routing, and RAG capabilities.

### Services (Ellie Host — `docker-compose.yml`)

| Service | Port | Purpose |
|---------|------|---------|
| `gateway` | 3000 | nginx reverse proxy — auth + routing |
| `open-webui` | (internal) | Web chat UI, user management, API keys, RAG |
| `ai-proxy` | 8081 | Model routing proxy (Anthropic + OpenAI formats) |
| `llama-server` | 8010 | GLM-4.7-Flash — primary generation model |
| `llama-embed` | 8011 | Qwen3-Embedding-0.6B — dense vector embeddings |
| `llama-rerank` | 8013 | Qwen3-Reranker-0.6B — cross-encoder reranking |
| `qdrant` | 6333 | Vector database for document search |
| `searxng` | 8888 | Private web search for RAG retrieval |
| `valkey` | 6379 | Redis-compatible cache for SearXNG |
| `fan-control` | (internal) | GPU fan/power curve controller |

### Services (Sparky Host — `docker-compose-sparky.yml`)

| Service | Port | Purpose |
|---------|------|---------|
| `host-metrics` | 3001 | GPU/host metrics collection (read-only NVML) |
| `vllm-qwen3-coder` | 8012 | Qwen3 Coder via vLLM |

## Architecture

```
Client → gateway (:3000)
           ├─ /v1/*      → auth_request (Open WebUI) → ai-proxy → backends
           ├─ /install/* → static setup scripts (claude-local)
           └─ /*         → Open WebUI (web interface)

ai-proxy routes by model name:
  "GLM 4.7 Flash"    → llama-server:8010 (Ellie)
  "Qwen3 Coder Next" → 10.20.10.10:8012 (Sparky vLLM)
  Claude model aliases → mapped to local models
```

- **Authentication**: Open WebUI manages users and `sk-*` API keys
- **Inference**: ai-proxy routes to correct backend based on `models.json` — no format translation
- **RAG**: Open WebUI → llama-embed (vectors) → Qdrant (storage) → llama-rerank (reranking) → SearXNG (web search)

## Key Files

| File | Purpose |
|------|---------|
| `ai-proxy/models.json` | Model routing config (names, URLs, aliases) |
| `ai-proxy/proxy.py` | Routing proxy — passes through Anthropic + OpenAI formats |
| `gateway/nginx.conf.template` | Auth subrequest + proxy routing |
| `gateway/install/` | claude-local setup scripts served at `/install/*` |
| `fan-control/main.py` | GPU fan/power control (pynvml, Ellie only) |
| `host-metrics/main.py` | GPU/host metrics (pynvml + psutil, Sparky) |
| `searxng/settings.yml` | SearXNG search engine config |
| `scripts/claude-local` | CLI wrapper to use Claude Code with local inference |
| `scripts/deploy-sparky.sh` | Remote deployment to Sparky via Docker context |
| `docs/claude-local-README.md` | Documentation for claude-local scripts |

## Common Commands

```bash
# Build and start all services
docker compose up -d

# Rebuild specific service
docker compose up -d --build ai-proxy
docker compose up -d --build gateway

# View logs
docker compose logs -f ai-proxy
docker compose logs -f llama-server
docker compose logs -f open-webui

# Test gateway auth
curl http://localhost:3000/v1/models -H "x-api-key: sk-YOUR_KEY"

# Test ai-proxy directly
curl -X POST http://localhost:8081/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"GLM 4.7 Flash","messages":[{"role":"user","content":"Hello"}]}'

# Deploy to Sparky
./scripts/deploy-sparky.sh
```

## Development Workflow

1. Edit source code in respective directories
2. Rebuild: `docker compose up -d --build <service-name>`
3. Test with curl/browser
4. Check logs: `docker compose logs -f <service-name>`

## Troubleshooting

### Gateway returns 401
- Verify your `sk-*` key exists in Open WebUI (Settings > API Keys)
- Test: `curl http://localhost:3000/v1/models -H "x-api-key: sk-..."`

### Model not found
- Check `ai-proxy/models.json` for correct model names and aliases
- Restart ai-proxy after config changes: `docker compose restart ai-proxy`

### llama-server not responding
- Check health: `curl http://localhost:8010/health`
- Review logs: `docker compose logs llama-server`
- Ensure sufficient VRAM for model

### Fan control not working
- Requires `privileged: true` in Docker
- Check logs: `docker logs fan-control`
- Verify `FAN_SETPOINTS` format: `50:30 70:65 78:95 80:100`
