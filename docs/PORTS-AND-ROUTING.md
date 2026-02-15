# AI Stack Ports and Routing Architecture

## Port Summary

| Port | Service | Purpose | External Access |
|------|---------|---------|-----------------|
| 3000 | temper-view | Frontend + Supabase proxy | **Required for login** |
| 8081 | ai-proxy | LLM inference API | **Required for Claude** |
| 8082 | llama-server | Internal LLM engine | Internal only |
| 3001 | temper (fan-manager) | GPU metrics | Proxied via 3000 |
| 8000 | stripe-handler | Billing webhooks | Proxied via 3000 |

## Routing Details

### Port 3000 (temper-view Nginx)

**Serves:**
- React frontend (SPA)
- Reverse proxy to Supabase
- Reverse proxy to temper metrics
- Reverse proxy to Stripe billing

**Routes:**
```nginx
/                → Static React app
/auth/v1/*       → Supabase Kong (authentication)
/rest/v1/*       → Supabase Kong (database REST API)
/api/*           → temper (GPU metrics on port 3001)
/api/billing/*   → stripe-handler (port 8000)
/chat/*          → llama-server (port 8082) with auto-auth
```

### Port 8081 (ai-proxy)

**Serves:**
- OpenAI-compatible LLM API
- API key validation via PostgreSQL
- Request rewriting and model routing

**Routes:**
```
/v1/chat/completions  → llama-server (port 8082)
/v1/models            → llama-server (port 8082)
```

**Does NOT handle:**
- ❌ Supabase authentication
- ❌ User management
- ❌ API key creation
- ❌ Metrics
- ❌ Billing

## claude-local Port Usage

### Login Feature (`--login`)

**Uses Port 3000:**
```
1. POST http://{IP}:3000/auth/v1/token?grant_type=password
   → Authenticate user with email/password
   → Returns JWT access token

2. GET http://{IP}:3000/rest/v1/api_keys
   → Authorization: Bearer {JWT}
   → Fetch user's API keys

3. POST http://{IP}:3000/rest/v1/api_keys
   → Authorization: Bearer {JWT}
   → Create new API key if needed
```

### Inference (`claude-local "prompt"`)

**Uses Port 8081:**
```
POST http://{IP}:8081/v1/chat/completions
→ Authorization: Bearer {API_KEY}
→ Forward to llama-server
```

## Firewall Configuration

### Minimum (Inference Only)

If you only need LLM inference and will set API keys manually:

```bash
# Allow ai-proxy access
sudo ufw allow 8081/tcp comment 'ai-proxy - LLM API'
```

Then use manual key setup:
```bash
./claude-local --set-key sk-ant-your-key
```

### Full Feature Set (Login + Inference)

For the complete experience including `--login`:

```bash
# Allow temper-view access (login, metrics, dashboard)
sudo ufw allow 3000/tcp comment 'temper-view - Web UI and Supabase'

# Allow ai-proxy access (LLM inference)
sudo ufw allow 8081/tcp comment 'ai-proxy - LLM API'
```

Then use login feature:
```bash
./claude-local --login
```

### Docker Compose Port Bindings

From `docker-compose.yml`:

```yaml
services:
  llama-server:
    ports:
      - "8082:8082"  # Internal only, not externally accessible

  ai-proxy:
    ports:
      - "8081:8081"  # External access for LLM API

  temper-view:
    ports:
      - "3000:80"    # External access for web UI + Supabase

  fan-manager:
    # No external port - proxied through temper-view:3000/api/*

  stripe-handler:
    # No external port - proxied through temper-view:3000/api/billing/*
```

## Security Implications

### Port 3000 Exposure

**Provides access to:**
- ✅ User authentication (Supabase)
- ✅ API key management (create, list, delete)
- ✅ GPU metrics dashboard
- ✅ Billing management
- ✅ User profiles

**Security measures:**
- Row-Level Security (RLS) on all tables
- JWT authentication required for sensitive endpoints
- Nginx security headers (CSP, X-Frame-Options, etc.)
- Rate limiting via Supabase

**Risk level:** Medium
- Requires valid user credentials to access data
- Public registration may need to be disabled in production
- Consider VPN or IP whitelisting for production deployments

### Port 8081 Exposure

**Provides access to:**
- ✅ LLM inference with API key authentication
- ✅ Model listing

**Security measures:**
- API key validation against PostgreSQL
- Rate limiting per user/tier
- Usage tracking and billing
- Request logging

**Risk level:** Medium-Low
- Requires valid API key
- Usage tracked and billed
- No direct database access

## Network Topology

```
Internet/LAN
    │
    ├─── Port 3000 (temper-view)
    │         │
    │         ├─→ Nginx (static files)
    │         ├─→ Supabase Kong:8000
    │         │     └─→ PostgreSQL (auth, api_keys, profiles)
    │         ├─→ temper:3001 (GPU metrics)
    │         ├─→ stripe-handler:8000 (billing)
    │         └─→ llama-server:8082 (chat UI, auto-auth)
    │
    └─── Port 8081 (ai-proxy)
              │
              ├─→ PostgreSQL (key validation)
              └─→ llama-server:8082 (inference)
```

## Troubleshooting

### Login fails with connection refused

**Problem:** Trying to use `--login` but port 3000 is not accessible

**Check:**
```bash
# Test if port 3000 is reachable
curl http://10.20.10.5:3000/

# Check if temper-view is running
docker ps | grep temper-view

# Check firewall
sudo ufw status | grep 3000
```

**Solutions:**
1. Start temper-view: `docker compose up -d temper-view`
2. Open firewall: `sudo ufw allow 3000/tcp`
3. Use manual setup instead: `./claude-local --set-key <key>`

### Inference works but login doesn't

**Problem:** Can use Claude with manual key, but `--login` fails

**Diagnosis:** Port 8081 is open but port 3000 is not

**Solution:**
```bash
# Option 1: Open port 3000 for login feature
sudo ufw allow 3000/tcp

# Option 2: Continue using manual key management
./claude-local --set-key sk-ant-your-existing-key
```

### Both ports timeout

**Problem:** Cannot reach either port

**Check:**
1. AI Stack is running: `docker compose ps`
2. Network connectivity: `ping 10.20.10.5`
3. Firewall on server: `sudo ufw status`
4. Firewall on router/network

## Best Practices

### Development Environment

```bash
# Open both ports for full functionality
sudo ufw allow 3000/tcp
sudo ufw allow 8081/tcp

# Use login feature
./claude-local --login
```

### Production Environment

```bash
# Option 1: VPN + open ports internally
# Open ports only to VPN subnet
sudo ufw allow from 10.8.0.0/24 to any port 3000
sudo ufw allow from 10.8.0.0/24 to any port 8081

# Option 2: Reverse proxy with authentication
# Put Nginx in front with HTTP basic auth or OAuth
# Only expose reverse proxy ports

# Option 3: API keys only
# Only expose port 8081
# Distribute API keys via secure channel
sudo ufw allow 8081/tcp
# Users use: ./claude-local --set-key <key>
```

### Home Network

```bash
# For trusted home network
sudo ufw allow from 192.168.1.0/24 to any port 3000
sudo ufw allow from 192.168.1.0/24 to any port 8081

# Use login feature from any device on home network
./claude-local --login
```

## Summary

**Answer to original question:**
> "Does all that route through the proxy? Or do I need to expose another port in my firewall?"

**Answer:** You need **port 3000** exposed in addition to port 8081.

- **Port 8081** (ai-proxy): LLM inference only
- **Port 3000** (temper-view): Login, auth, API key management, web UI

The login feature does NOT route through ai-proxy. It uses the temper-view Nginx reverse proxy to access Supabase on port 3000.

If you don't want to expose port 3000, you can still use the manual setup:
```bash
./claude-local --set-key sk-ant-your-key-here
```
