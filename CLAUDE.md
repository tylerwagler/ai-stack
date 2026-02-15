# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**For operational procedures, testing protocols, and deployment guidelines, see [AGENTS.md](./docs/AGENTS.md).**

## Working Files Policy

**All temporary and working files MUST go in the `/temp` directory.** This includes:

- Test scripts and one-off utilities
- SQL migrations under development
- Debug output and diagnostic files
- Scratch files and experiments
- Any file that isn't part of the permanent codebase

**Do NOT create files in the repository root.** The `/temp` directory is gitignored and can be freely used without polluting the project structure.

## System Overview

This is a **GPU-accelerated AI inference platform** with integrated billing, user management, and hardware monitoring. The stack consists of:

- **llama.cpp**: CUDA-based LLM inference engine serving GLM-4.7-Flash and Nemotron-3-Nano models
- **llama-proxy**: Python HTTP proxy providing authentication, model routing, and request rewriting
- **temper**: C++ GPU telemetry and thermal control system (NVIDIA NVML)
- **ai-portal**: React 19 web dashboard for GPU monitoring and API management
- **stripe-handler**: FastAPI service for Stripe subscription billing
- **supabase-ai**: PostgreSQL database with authentication backend

## Architecture

### Distributed Monitoring (as of 2026-02-15)

**Split Services Model**: The monolithic fan-manager has been split into two independent services:

```
ai-portal (Port 3000) → Nginx Reverse Proxy
├─ /api/metrics → host-metrics:3001 (read-only GPU/host metrics)
├─ /api/* → llama-proxy:8081 (model routing)
├─ /auth, /rest → Supabase Kong
└─ SPA assets

[Ellie Host]
├─ host-metrics (Port 3001): GPU/host metrics collection (read-only via NVML)
├─ fan-control (internal): GPU fan/power control (writes via NVML)
└─ ai-portal: Web dashboard with multi-host aggregation

[Sparky Host]
├─ host-metrics (Port 3001): GPU/host metrics collection (read-only)
└─ (No fan control - vLLM handles resource management)

LLM Requests → llama-proxy (Port 8081) → llama-server/vLLM (Port 8082/8000)
                    ↓                           ↓
              PostgreSQL DB                 CUDA GPUs
              (API key validation)          (model inference)
```

### Key Data Flows

1. **GPU Metrics (Distributed)**:
   - host-metrics on each host polls NVML every 100ms (read-only, no writes)
   - Exposes `/metrics` JSON API on port 3001
   - ai-portal fetches from both Ellie and Sparky endpoints in parallel
   - Frontend aggregates multi-host metrics and displays per-host sections

2. **Fan Control (Ellie Only)**:
   - fan-control service runs privileged on Ellie only
   - Monitors GPU temps and power via NVML reads
   - Applies dynamic fan curves and power limits via NVML writes
   - No HTTP API (internal monitoring only)
   - Independent from metrics collection - failure doesn't affect monitoring

3. **LLM Inference**: Client sends request with API key → llama-proxy validates against DB → forwards to llama-server/vLLM → streams response back

4. **Authentication**: User logs in via Supabase Auth → JWT stored in browser → used for API key management and billing

## Common Development Commands

### Build & Deploy

```bash
# Build and start all services
docker compose up -d

# Rebuild specific service after code changes
docker compose up -d --build host-metrics    # Read-only metrics (Ellie + Sparky)
docker compose up -d --build fan-control     # Active fan control (Ellie only)
docker compose up -d --build ai-portal     # Web dashboard
docker compose up -d --build llama-proxy     # Model router

# View logs
docker compose logs -f host-metrics
docker compose logs -f fan-control
docker compose logs -f llama-server
docker compose logs -f llama-proxy

# Full update (downloads models, rebuilds images, restarts)
./scripts/update.sh

# Restore from backup
./scripts/update.sh --restore
```

### Testing Individual Components

```bash
# Test host-metrics API (requires METRICS_API_KEY from .env)
curl -s -H "x-api-key: $METRICS_API_KEY" http://localhost:3001/metrics | jq .

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
cd ai-portal
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
Control setpoints (environment variables):
```bash
# host-metrics service (read-only, no controls)
TEMPER_MODE=metrics                             # Read-only mode (no fan/power writes)

# fan-control service (active control, Ellie only)
TEMPER_MODE=fanctl                              # Active fan control mode
FAN_SETPOINTS=50:30 70:65 78:95 80:100          # GPU temp(°C):fan%
POWER_SETPOINTS=70:230 80:175 85:125            # GPU temp:power_limit_watts
```

## Component-Specific Details

### temper (C++ GPU Monitoring & Control)

**Split Architecture**: Now supports two independent modes via `TEMPER_MODE` environment variable

**Build**: Makefile with NVML dependency detection
- Sources: `src/main.cpp`, `NVMLManager.cpp`, `CurveController.cpp`, `IpmiController.cpp`, `MetricServer.cpp`, `HostMonitor.cpp`, `LlamaMonitor.cpp`
- Output: `build/temper` binary
- Docker: Runs privileged with GPU passthrough and CUDA 13.1 base
- Entrypoint: `entrypoint.sh` selects mode based on `TEMPER_MODE` environment variable

**Dual Modes**:

1. **metrics mode** (host-metrics service):
   - Read-only GPU/host data collection via NVML
   - Exposes `/metrics` JSON API on port 3001
   - NO fan speed writes, NO power limit writes
   - Runs on both Ellie and Sparky
   - Lightweight (just polling and serving data)

2. **fanctl mode** (fan-control service):
   - Active GPU fan and power control
   - Reads temperatures and applies control curves
   - Writes fan speeds and power limits via NVML
   - Runs only on Ellie (Sparky doesn't need fan control)
   - No HTTP API (internal monitoring only)
   - Reactive fallback: Cuts power to minimum if thermal throttling detected

**Key Classes**:
- `NVMLManager`: Wraps NVIDIA NVML library for GPU queries and writes
- `CurveController`: Interpolates fan/power curves (fanctl mode only)
- `MetricServer`: HTTP server exposing `/metrics` JSON endpoint on port 3001 (metrics mode only)
- `LlamaMonitor`: Polls llama-server for workload status (fanctl mode only; metrics mode skips this)
- `HostMonitor`: Reads CPU temp, RAM usage, uptime from /proc and /sys (both modes)

**API Schema**: See `temper/API.md` for full `/metrics` response structure (100+ fields)

**Deployment**:
- Ellie runs both: `host-metrics` (port 3001) and `fan-control` (internal)
- Sparky runs only: `host-metrics` (port 3001)

### llama-proxy (Python Gateway)

**Purpose**: Validates API keys, rewrites model names, forwards to llama-server
- Database-backed auth with 60-second cache to reduce DB load
- Translates Claude model names (e.g., `claude-3-5-sonnet-20241022`) to configured model (`glm` or `nemotron`)
- Streaming support via chunked transfer encoding
- Logs to `/app/logs/` (mounted volume)

**Dependencies**: Python 3.11, psycopg2, HTTP server stdlib

### ai-portal (React Frontend)

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
- **Internal Auth**: `LLAMA_API_KEY` protects llama-server from direct access (only llama-proxy knows it)
- **Metrics Auth**: `METRICS_API_KEY` required for accessing host-metrics `/metrics` endpoint
- **CSP Headers**: Nginx applies Content Security Policy allowing only trusted origins
- **Network Isolation**:
  - llama-server binds to 127.0.0.1 only (internal network)
  - fan-control has no HTTP API (no network exposure)
  - host-metrics port 3001 exposed only within Docker network and firewall
- **Stripe Webhooks**: Validated via `STRIPE_WEBHOOK_SECRET` before processing subscription events
- **Privilege Separation**:
  - host-metrics runs privileged for NVML read access (non-destructive)
  - fan-control runs privileged for NVML write access (fan/power control only)

## Hardware Requirements

- **GPU**: NVIDIA GPU(s) with CUDA 13.1+ support (tested on multi-GPU setups)
- **RAM**: Minimum 16GB system RAM (models are 4-30GB each)
- **Disk**: ~40GB for models (stored in `llama_cache` volume)
- **Optional**: Dell iDRAC for chassis fan control (IPMI over LAN)

## Troubleshooting

### GPU not detected in host-metrics or fan-control
- Verify `docker compose` includes `deploy.resources.reservations.devices` with nvidia driver
- Check `nvidia-smi` works on host
- Ensure NVIDIA Container Toolkit is installed
- Restart service: `docker compose up -d --build host-metrics` or `fan-control`

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
- Check Nginx logs: `docker compose logs ai-portal`
- Verify environment variables passed to container
- Test direct API access: `curl http://localhost:3001/metrics`

### Fan control not working
- Requires root/privileged mode in Docker
- Check NVML initialization in logs: `docker logs fan-control`
- Verify `FAN_SETPOINTS` environment variable format: `50:30 70:65 78:95 80:100`
- IPMI control requires network access to `IDRAC_IP`
- fan-control service fails silently if fan-speed writes aren't supported; check GPU compatibility

### host-metrics returns 401 Unauthorized
- Verify `METRICS_API_KEY` is set in `.env`
- Check request header: must be `x-api-key: <key>` or `Authorization: Bearer <key>` (case-insensitive)
- Test directly: `curl -H "x-api-key: $METRICS_API_KEY" http://localhost:3001/metrics`

### Multi-host metrics not aggregating
- Verify Sparky's host-metrics is running: `docker compose ps` on Sparky
- Test Sparky endpoint from Ellie: `curl http://10.20.10.10:3001/metrics -H "x-api-key: ..."`
- Check frontend console for fetch errors: open http://ellie:3000/ and check browser console
- Frontend default includes both `/api` (local) and `http://10.20.10.10:3001` (Sparky)

## Development Workflow

1. **Make changes** to source code in respective directories
2. **Rebuild** affected service: `docker compose up -d --build <service-name>`
3. **Test** using curl/browser against service endpoints
   - Run `./scripts/integration-test.sh` for full stack validation
   - Run `./scripts/security-test.sh` before production deployment
4. **Check logs** for errors: `docker compose logs -f <service-name>`
5. **Commit** changes following git workflow in [AGENTS.md](./docs/AGENTS.md#3-git-workflow)

For frontend changes, use `npm run dev` in `ai-portal/` for hot reload during development.

**IMPORTANT: See [AGENTS.md](./docs/AGENTS.md) for:**
- Comprehensive testing protocols
- Change management procedures
- Security guidelines
- Git commit standards
- Credential management
- Troubleshooting procedures

## Model Management

**Swap models**: Edit `llama-proxy` environment variable `DEFAULT_MODEL` to `glm` or `nemotron`, then:
```bash
docker compose up -d llama-proxy
```

**Add new model**:
1. Download GGUF file to `llama_cache` volume
2. Add entry to `/models.ini`
3. Restart llama-server: `docker compose restart llama-server`

**Update existing models**: Use `./scripts/update.sh` which handles backup, download, and restart
