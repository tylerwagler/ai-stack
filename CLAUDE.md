# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## System Overview

This is a **GPU-accelerated AI inference platform** with integrated billing, user management, and hardware monitoring. The stack consists of:

- **llama.cpp**: CUDA-based LLM inference engine serving GLM-4.7-Flash and Nemotron-3-Nano models
- **llama-proxy**: Python HTTP proxy providing authentication, model routing, and request rewriting
- **temper**: C++ GPU telemetry and thermal control system (NVIDIA NVML)
- **temper-view**: React 19 web dashboard for GPU monitoring and API management
- **stripe-handler**: FastAPI service for Stripe subscription billing
- **supabase-ai**: PostgreSQL database with authentication backend

## Architecture

```
User → temper-view (Port 3000) → Nginx Reverse Proxy
                                    ├→ /api → fan-manager (temper:3001)
                                    ├→ /auth, /rest → Supabase Kong
                                    └→ SPA assets

LLM Requests → llama-proxy (Port 8081) → llama-server (Port 8082)
                    ↓                           ↓
              PostgreSQL DB                 CUDA GPUs
              (API key validation)          (model inference)
```

### Key Data Flows

1. **GPU Metrics**: temper polls NVML every 100ms → exposes `/metrics` JSON API → temper-view fetches and displays
2. **LLM Inference**: Client sends request with API key → llama-proxy validates against DB → forwards to llama-server → streams response back
3. **Authentication**: User logs in via Supabase Auth → JWT stored in browser → used for API key management and billing
4. **Fan Control**: temper runs dynamic fan curves based on GPU/CPU temps and power limits in real-time control loop

## Common Development Commands

### Build & Deploy

```bash
# Build and start all services
docker compose up -d

# Rebuild specific service after code changes
docker compose up -d --build fan-manager
docker compose up -d --build temper-view
docker compose up -d --build llama-proxy

# View logs
docker compose logs -f fan-manager
docker compose logs -f llama-server
docker compose logs -f llama-proxy

# Full update (downloads models, rebuilds images, restarts)
./update.sh

# Restore from backup
./update.sh --restore
```

### Testing Individual Components

```bash
# Test temper metrics API
curl -s http://localhost:3001/metrics | jq .

# Test llama-server health (requires LLAMA_API_KEY from .env)
curl -H "Authorization: Bearer $LLAMA_API_KEY" http://localhost:8082/chat/health

# Test llama-proxy (requires valid user API key)
curl -X POST http://localhost:8081/v1/chat/completions \
  -H "Authorization: Bearer sk-ant-YOUR_USER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-3-5-sonnet-20241022","messages":[{"role":"user","content":"Hello"}]}'

# Manual temper build (for development)
cd temper && make clean && make
```

### Frontend Development

```bash
# Start dev server with hot reload
cd temper-view
npm install
npm run dev  # Runs on port 5173

# Production build
npm run build

# Preview production build
npm run preview
```

### Database Access

```bash
# Connect to PostgreSQL
docker exec -it ai-supabase-db-1 psql -U postgres -d postgres

# View API keys
SELECT id, key_prefix, created_at FROM api_keys;

# View user profiles
SELECT id, email, subscription_status, stripe_customer_id FROM profiles;
```

## Critical Configuration Files

### `/models.ini`
Defines LLM model configurations:
- `glm`: GLM-4.7-Flash with 200K context, tensor-split 22,25 (multi-GPU)
- `nemotron`: Nemotron-3-Nano-30B with 200K context, tensor-split 25,27

**Important**: Only one model loads at a time (`models-max 1` in docker-compose.yml). Model swapping is handled by llama-proxy's DEFAULT_MODEL env var.

### `/.env`
Required environment variables:
```bash
LLAMA_API_KEY=sk-ant-...          # Internal auth for llama-server
METRICS_API_KEY=...               # Auth for temper metrics endpoint
POSTGRES_PASSWORD=...             # Supabase database password
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
IDRAC_IP=10.20.20.3              # Dell iDRAC IP (optional)
IDRAC_USER=temper
IDRAC_PASS=...
```

### `/docker-compose.yml`
Fan control setpoints (environment variables for `fan-manager`):
```bash
FAN_SETPOINTS=50:30 70:65 78:95 80:100           # GPU temp(°C):fan%
CHASSIS_FAN_SETPOINTS=45:20 55:30 65:70 75:100  # CPU temp:chassis fan%
POWER_SETPOINTS=70:230 80:175 85:125            # GPU temp:power_limit_watts
```

## Component-Specific Details

### temper (C++ GPU Control)

**Build**: Makefile with NVML dependency detection
- Sources: `src/main.cpp`, `NVMLManager.cpp`, `CurveController.cpp`, `IpmiController.cpp`, `MetricServer.cpp`, `HostMonitor.cpp`, `LlamaMonitor.cpp`
- Output: `build/temper` binary
- Docker: Runs privileged with GPU passthrough and CUDA 13.1 base

**Key Classes**:
- `NVMLManager`: Wraps NVIDIA NVML library for GPU queries
- `CurveController`: Applies dynamic fan curves and power limits (runs in tight loop)
- `IpmiController`: Communicates with iDRAC for chassis fan control via IPMItool
- `MetricServer`: HTTP server exposing `/metrics` JSON endpoint on port 3001
- `LlamaMonitor`: Polls llama-server for workload status and KV cache usage
- `HostMonitor`: Reads CPU temp, RAM usage, uptime from /proc and /sys

**API Schema**: See `temper/API.md` for full `/metrics` response structure (100+ fields)

### llama-proxy (Python Gateway)

**Purpose**: Validates API keys, rewrites model names, forwards to llama-server
- Database-backed auth with 60-second cache to reduce DB load
- Translates Claude model names (e.g., `claude-3-5-sonnet-20241022`) to configured model (`glm` or `nemotron`)
- Streaming support via chunked transfer encoding
- Logs to `/app/logs/` (mounted volume)

**Dependencies**: Python 3.11, psycopg2, HTTP server stdlib

### temper-view (React Frontend)

**Tech Stack**:
- React 19.2.3 with TypeScript
- Vite for build tooling
- TailwindCSS 4.x with PostCSS
- TanStack React Query v5 for data fetching
- Recharts for GPU metric visualization
- Supabase JS client for auth
- React Router v7 for navigation

**Key Components**:
- `src/Portal.tsx`: Main entry point with auth and tab routing
- `src/components/GPUDashboard.tsx`: Multi-GPU metrics display
- `src/components/charts/`: Specialized chart components (PowerChart, TempChart, MemoryChart, etc.)
- `src/components/SettingsPage.tsx`: API key management and configuration

**Nginx Config**: Reverse proxies `/api/*` to temper, `/auth/*` and `/rest/*` to Supabase Kong, serves SPA from root

**Build**: Multi-stage Dockerfile (Node 20 build → Nginx alpine runtime)

### llama.cpp (Inference Engine)

**Build**: Custom CUDA 13.1 Dockerfile in `.devops/cuda-new.Dockerfile`
- Multi-stage: builder → server target
- Includes GGUF model support with quantized KV cache (q8_0)
- Jinja template support for chat formatting
- Metrics exposed via `/chat/metrics` endpoint

**Runtime**: Binds to all GPUs, serves on port 8082 with OpenAI-compatible API

## Security Considerations

- **API Keys**: User keys stored in PostgreSQL `api_keys` table, validated on every llama-proxy request
- **Internal Auth**: `LLAMA_API_KEY` protects llama-server from direct access (only llama-proxy and temper know it)
- **Metrics Auth**: `METRICS_API_KEY` required for accessing temper's `/metrics` endpoint
- **CSP Headers**: Nginx applies Content Security Policy allowing only trusted origins
- **Network Isolation**: llama-server and temper only accessible via internal Docker network (127.0.0.1 binding or no external port)
- **Stripe Webhooks**: Validated via `STRIPE_WEBHOOK_SECRET` before processing subscription events

## Hardware Requirements

- **GPU**: NVIDIA GPU(s) with CUDA 13.1+ support (tested on multi-GPU setups)
- **RAM**: Minimum 16GB system RAM (models are 4-30GB each)
- **Disk**: ~40GB for models (stored in `llama_cache` volume)
- **Optional**: Dell iDRAC for chassis fan control (IPMI over LAN)

## Troubleshooting

### GPU not detected in temper
- Verify `docker compose` includes `deploy.resources.reservations.devices` with nvidia driver
- Check `nvidia-smi` works on host
- Ensure NVIDIA Container Toolkit is installed

### Model loading fails in llama-server
- Check `models.ini` paths match filenames in `llama_cache` volume
- Verify models downloaded: `docker volume inspect ai-stack_llama_cache`
- Review startup logs: `docker compose logs llama-server`
- Ensure sufficient VRAM for model size

### llama-proxy returns 401 Unauthorized
- Verify API key exists in database: `SELECT * FROM api_keys;`
- Check `llama-proxy` logs for cache hits/misses
- Ensure PostgreSQL is healthy: `docker compose ps db`

### Frontend not loading
- Check Nginx logs: `docker compose logs temper-view`
- Verify environment variables passed to container
- Test direct API access: `curl http://localhost:3001/metrics`

### Fan control not working
- Requires root/privileged mode in Docker
- Check NVML initialization in logs
- Verify `FAN_SETPOINTS` environment variable format
- IPMI control requires network access to `IDRAC_IP`

## Development Workflow

1. **Make changes** to source code in respective directories
2. **Rebuild** affected service: `docker compose up -d --build <service-name>`
3. **Test** using curl/browser against service endpoints
4. **Check logs** for errors: `docker compose logs -f <service-name>`
5. **Commit** changes (this is not a git repository currently)

For frontend changes, use `npm run dev` in `temper-view/` for hot reload during development.

## Model Management

**Swap models**: Edit `llama-proxy` environment variable `DEFAULT_MODEL` to `glm` or `nemotron`, then:
```bash
docker compose up -d llama-proxy
```

**Add new model**:
1. Download GGUF file to `llama_cache` volume
2. Add entry to `/models.ini`
3. Restart llama-server: `docker compose restart llama-server`

**Update existing models**: Use `./update.sh` which handles backup, download, and restart
