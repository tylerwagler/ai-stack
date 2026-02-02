# Authentication Guide - ai-stack

**Last Updated:** 2026-02-02

This document provides comprehensive guidance on authentication in the ai-stack system, covering all API keys, their purposes, configuration, and troubleshooting.

## Table of Contents

1. [Overview](#overview)
2. [API Keys](#api-keys)
3. [Configuration](#configuration)
4. [Data Flows](#data-flows)
5. [Security Best Practices](#security-best-practices)
6. [Troubleshooting](#troubleshooting)
7. [Key Rotation](#key-rotation)

## Overview

The ai-stack uses a **three-tier authentication system** with distinct API keys serving different security boundaries:

```
┌─────────────────────────────────────────────────────────────┐
│                       Security Layers                        │
├─────────────────────────────────────────────────────────────┤
│ Layer 1: Internal (LLAMA_API_KEY)                          │
│   temper ←→ llama-server                                    │
├─────────────────────────────────────────────────────────────┤
│ Layer 2: Metrics (METRICS_API_KEY)                         │
│   nginx → temper                                             │
│   browser → nginx → temper                                   │
├─────────────────────────────────────────────────────────────┤
│ Layer 3: User (User API Keys)                              │
│   user clients → llama-proxy → PostgreSQL validation       │
└─────────────────────────────────────────────────────────────┘
```

This separation ensures:
- **Internal components** can communicate without exposing credentials to users
- **Monitoring endpoints** are protected but accessible to the frontend
- **User requests** are authenticated against a database with proper user management

## API Keys

### 1. LLAMA_API_KEY

**Purpose:** Authenticates internal requests to llama-server

**Format:** `sk-ant-{base64_random_string}`

**Used By:**
- temper's LlamaMonitor (polls every 100ms for metrics)
- Docker healthcheck for llama-server container
- ModelManager for model switching operations

**NOT Used By:**
- End users
- Frontend (temper-view)
- llama-proxy (uses its own auth)

**Security Level:** HIGH - exposes full llama-server functionality

**Lifetime:** Permanent (until manually rotated)

**Storage:**
- `.env` file: `LLAMA_API_KEY=sk-ant-...`
- Docker environment variables (runtime only)
- Never logged in full (only prefix like `sk-ant-xxx...`)

**Example Usage:**
```bash
curl -H "Authorization: Bearer $LLAMA_API_KEY" \
  http://localhost:8082/chat/health
```

---

### 2. METRICS_API_KEY

**Purpose:** Protects temper's metrics endpoint from unauthorized access

**Format:** Base64-encoded random string (32+ bytes)

**Used By:**
- nginx (automatically injected via `proxy_set_header`)
- Direct API clients (if bypassing nginx)
- Testing scripts and monitoring tools

**NOT Used By:**
- Frontend JavaScript (nginx injects header automatically)
- llama-server
- llama-proxy

**Security Level:** MEDIUM - exposes read-only system metrics

**Lifetime:** Permanent (until manually rotated)

**Storage:**
- `.env` file: `METRICS_API_KEY=...`
- nginx configuration (as environment variable)
- temper validates on every request

**Example Usage:**
```bash
curl -H "X-API-Key: $METRICS_API_KEY" \
  http://localhost:3001/metrics
```

---

### 3. User API Keys

**Purpose:** Authenticate end users for LLM inference via llama-proxy

**Format:** `sk-ant-{user_random_string}` (user-specific)

**Used By:**
- End users making requests to llama-proxy
- User applications and integrations
- API clients like curl, Postman, SDKs

**NOT Used By:**
- Internal monitoring
- temper metrics
- llama-server direct access

**Security Level:** MEDIUM - limited to inference requests with rate limiting

**Lifetime:** Until user revokes or admin deletes

**Storage:**
- PostgreSQL `api_keys` table (full key hashed, prefix stored)
- User browser (when logged in to temper-view)
- User's secure storage (keys shown once on creation)

**Validation:**
1. llama-proxy receives request with user API key
2. Queries PostgreSQL: `SELECT * FROM api_keys WHERE key = ?`
3. Checks subscription status and rate limits
4. Caches result for 60 seconds
5. Forwards to llama-server with LLAMA_API_KEY

**Example Usage:**
```bash
curl -X POST http://localhost:8081/v1/chat/completions \
  -H "Authorization: Bearer sk-ant-YOUR_USER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-3-5-sonnet-20241022","messages":[{"role":"user","content":"Hello"}]}'
```

---

## Configuration

### Initial Setup

1. **Generate Keys:**

```bash
# Generate LLAMA_API_KEY
LLAMA_API_KEY="sk-ant-$(openssl rand -base64 32)"

# Generate METRICS_API_KEY
METRICS_API_KEY="$(openssl rand -base64 32)"

echo "LLAMA_API_KEY=$LLAMA_API_KEY"
echo "METRICS_API_KEY=$METRICS_API_KEY"
```

2. **Add to .env File:**

```bash
cat >> .env <<EOF
LLAMA_API_KEY=$LLAMA_API_KEY
METRICS_API_KEY=$METRICS_API_KEY
EOF
```

3. **Verify .env is Gitignored:**

```bash
# Ensure .env is in .gitignore
grep -q "^\.env$" .gitignore || echo ".env" >> .gitignore
```

4. **Set Permissions:**

```bash
chmod 600 .env  # Read/write for owner only
```

### Docker Compose Configuration

The keys are passed to containers via environment variables:

```yaml
# llama-server
environment:
  - LLAMA_API_KEY=${LLAMA_API_KEY}

# fan-manager (temper)
environment:
  - LLAMA_API_KEY=${LLAMA_API_KEY}
  - METRICS_API_KEY=${METRICS_API_KEY}

# temper-view (nginx)
environment:
  - METRICS_API_KEY=${METRICS_API_KEY}
```

### Nginx Configuration

nginx injects the METRICS_API_KEY automatically:

```nginx
location /api/ {
    proxy_pass http://fan-manager:3001/;
    proxy_set_header X-API-Key "${METRICS_API_KEY}";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

**Important:** The `${METRICS_API_KEY}` substitution happens at container startup via envsubst or similar mechanism.

### User API Key Creation

User API keys are created via the temper-view dashboard:

1. User logs in with Supabase Auth (JWT)
2. Navigates to Settings → API Keys
3. Clicks "Create New API Key"
4. Frontend calls backend API (authenticated with JWT)
5. Backend generates key: `sk-ant-{random}`
6. Key is hashed and stored in PostgreSQL
7. Full key shown ONCE to user (copy to clipboard)
8. User stores key securely (password manager, env var, etc.)

**Database Schema:**

```sql
CREATE TABLE api_keys (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES auth.users(id),
    key TEXT UNIQUE NOT NULL,  -- Full key (hashed in production)
    key_prefix TEXT NOT NULL,  -- First 12 chars for display (e.g., "sk-ant-abc123...")
    name TEXT,                 -- User-provided name for key
    created_at TIMESTAMP DEFAULT NOW(),
    last_used_at TIMESTAMP,
    revoked BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_api_keys_key ON api_keys(key);
CREATE INDEX idx_api_keys_user_id ON api_keys(user_id);
```

---

## Data Flows

### Flow 1: Metrics Collection (temper → llama-server)

```
┌─────────┐                                  ┌──────────────┐
│ temper  │                                  │ llama-server │
│ (C++)   │                                  │  (port 8082) │
└────┬────┘                                  └──────┬───────┘
     │                                              │
     │  GET /chat/health                           │
     │  Authorization: Bearer {LLAMA_API_KEY}      │
     ├─────────────────────────────────────────────>│
     │                                              │
     │  200 OK                                      │
     │  {"status": "ok"}                            │
     │<─────────────────────────────────────────────┤
     │                                              │
     │  GET /chat/v1/models                         │
     │  Authorization: Bearer {LLAMA_API_KEY}       │
     ├─────────────────────────────────────────────>│
     │                                              │
     │  200 OK                                      │
     │  {"models": [...]}                           │
     │<─────────────────────────────────────────────┤
     │                                              │
     │  GET /chat/slots                             │
     │  Authorization: Bearer {LLAMA_API_KEY}       │
     ├─────────────────────────────────────────────>│
     │                                              │
     │  200 OK                                      │
     │  [{"id": 0, "state": "idle"}, ...]           │
     │<─────────────────────────────────────────────┤
     │                                              │
     │  GET /chat/metrics                           │
     │  Authorization: Bearer {LLAMA_API_KEY}       │
     ├─────────────────────────────────────────────>│
     │                                              │
     │  200 OK                                      │
     │  llamacpp:prompt_tokens_total 1234           │
     │<─────────────────────────────────────────────┤
     │                                              │
     │  Updates LlamaMetrics struct                 │
     │                                              │
```

**Frequency:** Every 100ms (10 times per second)

**Authentication:** LLAMA_API_KEY in Authorization header

**Failure Handling:**
- Timeout → Keep previous metrics, set status=OFFLINE
- 401 Unauthorized → Log error, keep retrying
- 503 Service Unavailable → Set status=LOADING

---

### Flow 2: Frontend Metrics Display (Browser → nginx → temper)

```
┌─────────┐      ┌───────┐      ┌─────────┐
│ Browser │      │ nginx │      │ temper  │
│ (React) │      │ :3000 │      │  :3001  │
└────┬────┘      └───┬───┘      └────┬────┘
     │               │               │
     │  GET /api/metrics              │
     │  (no auth header)              │
     ├──────────────>│               │
     │               │               │
     │               │  GET /metrics │
     │               │  X-API-Key: {METRICS_API_KEY}
     │               ├──────────────>│
     │               │               │
     │               │  200 OK       │
     │               │  {...metrics} │
     │               │<──────────────┤
     │               │               │
     │  200 OK       │               │
     │  {...metrics} │               │
     │<──────────────┤               │
     │               │               │
     │  Renders GPU Dashboard        │
     │                              │
```

**Frequency:** Every 100ms (React Query refetchInterval)

**Authentication:** None required from browser (nginx injects header)

**nginx Behavior:**
1. Receives request at `/api/metrics`
2. Proxies to `http://fan-manager:3001/metrics`
3. Automatically adds `X-API-Key: ${METRICS_API_KEY}` header
4. Returns response to browser

**Why This Design?**
- Keeps METRICS_API_KEY out of frontend JavaScript
- Single configuration point (nginx env var)
- Prevents accidental exposure in browser console/network tab

---

### Flow 3: User Inference Request (User → llama-proxy → llama-server)

```
┌──────┐      ┌─────────────┐      ┌──────────────┐      ┌──────────────┐
│ User │      │ llama-proxy │      │  PostgreSQL  │      │ llama-server │
│      │      │    :8081    │      │      DB      │      │    :8082     │
└──┬───┘      └──────┬──────┘      └──────┬───────┘      └──────┬───────┘
   │                 │                     │                     │
   │  POST /v1/chat/completions            │                     │
   │  Authorization: Bearer {USER_KEY}     │                     │
   ├────────────────>│                     │                     │
   │                 │                     │                     │
   │                 │  SELECT * FROM api_keys                    │
   │                 │  WHERE key = {USER_KEY}                    │
   │                 ├────────────────────>│                     │
   │                 │                     │                     │
   │                 │  Row returned       │                     │
   │                 │  (valid, not revoked)                     │
   │                 │<────────────────────┤                     │
   │                 │                     │                     │
   │                 │  Cache result (60s) │                     │
   │                 │                     │                     │
   │                 │  POST /chat/v1/chat/completions           │
   │                 │  Authorization: Bearer {LLAMA_API_KEY}    │
   │                 ├───────────────────────────────────────────>│
   │                 │                     │                     │
   │                 │  200 OK (streaming)│                     │
   │                 │  data: {...}       │                     │
   │                 │<───────────────────────────────────────────┤
   │                 │                     │                     │
   │  200 OK (streaming)                  │                     │
   │  data: {...}    │                     │                     │
   │<────────────────┤                     │                     │
   │                 │                     │                     │
```

**Frequency:** On-demand (user requests)

**Authentication:** Two-stage
1. User API key validated against PostgreSQL
2. Internal LLAMA_API_KEY used to forward to llama-server

**Caching:** 60-second cache per user API key (reduces DB load)

**Model Name Rewriting:**

llama-proxy translates Claude-style model names to actual models:

```python
# Example mapping
MODEL_MAP = {
    "claude-3-5-sonnet-20241022": os.getenv("DEFAULT_MODEL", "glm"),
    "claude-3-5-haiku-20241022": os.getenv("DEFAULT_MODEL", "glm"),
    # Other names passed through unchanged
}
```

---

## Security Best Practices

### 1. Key Storage

**DO:**
- ✅ Store keys in `.env` file (gitignored)
- ✅ Use environment variables in production
- ✅ Set file permissions to 600 (owner read/write only)
- ✅ Use secrets management (HashiCorp Vault, AWS Secrets Manager) in enterprise deployments
- ✅ Rotate keys periodically (every 90 days recommended)

**DON'T:**
- ❌ Commit keys to git repositories
- ❌ Store keys in frontend JavaScript
- ❌ Log full keys (log prefix only)
- ❌ Share keys via insecure channels (email, Slack)
- ❌ Reuse keys across environments (dev/staging/prod)

### 2. Key Generation

Always use cryptographically secure random number generators:

```bash
# Good - LLAMA_API_KEY
sk-ant-$(openssl rand -base64 32)

# Good - METRICS_API_KEY
openssl rand -base64 32

# Bad - predictable
sk-ant-12345

# Bad - short
sk-ant-abc
```

**Minimum Length:** 32 bytes (base64-encoded = 44 characters)

### 3. Network Isolation

**Internal Keys (LLAMA_API_KEY, METRICS_API_KEY):**
- Only accessible within Docker network
- temper metrics bound to 127.0.0.1 (localhost only)
- llama-server port 8082 exposed for development (should be internal-only in production)

**Production Hardening:**

```yaml
# docker-compose.yml - Production
services:
  llama-server:
    ports:
      - "127.0.0.1:8082:8082"  # Bind to localhost only

  fan-manager:
    ports:
      - "127.0.0.1:3001:3001"  # Already localhost-only
```

### 4. Log Sanitization

Never log full API keys:

```cpp
// Good - temper C++ code
if (std::getenv("VERBOSE")) {
    std::string keyPrefix = apiKey_.substr(0, 12);
    std::cout << "[Llama] Using API key: " << keyPrefix << "..." << std::endl;
}

// Bad
std::cout << "[Llama] API key: " << apiKey_ << std::endl;  // NEVER DO THIS
```

```python
# Good - llama-proxy Python code
key_prefix = user_key[:12]
logger.info(f"Validated API key: {key_prefix}...")

# Bad
logger.info(f"Validated API key: {user_key}")  # NEVER DO THIS
```

### 5. Key Rotation Schedule

| Key Type | Rotation Frequency | Reason |
|----------|-------------------|---------|
| LLAMA_API_KEY | Every 90 days | Internal security best practice |
| METRICS_API_KEY | Every 90 days | Limit exposure window |
| User API Keys | User-controlled | Users manage their own keys |

### 6. Monitoring Failed Auth Attempts

**Implement Alerting:**

```bash
# Monitor llama-server 401 responses
docker compose logs llama-server | grep -i "401\|unauthorized"

# Monitor temper 401 responses
docker compose logs fan-manager | grep -i "401\|unauthorized"

# Monitor llama-proxy failed validations
docker compose logs llama-proxy | grep -i "invalid.*key"
```

**Set Up Alerts:**
- Threshold: >10 failed attempts in 5 minutes
- Action: Alert admin, consider key compromise

---

## Troubleshooting

### Problem: "Invalid API Key" on llama-server

**Symptoms:**
```
{"error":{"message":"Invalid API Key","type":"authentication_error","code":401}}
```

**Diagnosis:**

1. Check LLAMA_API_KEY is set in .env:
```bash
cat .env | grep LLAMA_API_KEY
```

2. Verify key is loaded in containers:
```bash
docker exec fan-manager printenv LLAMA_API_KEY
docker exec llama-server printenv LLAMA_API_KEY
```

3. Test key manually:
```bash
export $(cat .env | grep LLAMA_API_KEY)
curl -H "Authorization: Bearer $LLAMA_API_KEY" http://localhost:8082/chat/health
```

**Solutions:**

- **Key not in .env:** Add it and restart containers
- **Key mismatch:** Ensure same key in both fan-manager and llama-server
- **Container not restarted:** `docker compose down && docker compose up -d`
- **Typo in key:** Regenerate and update

---

### Problem: Frontend shows "Unauthorized" for metrics

**Symptoms:**
- Browser console shows 401 on `/api/metrics`
- Dashboard displays "Unable to fetch metrics"

**Diagnosis:**

1. Check nginx has METRICS_API_KEY:
```bash
docker exec temper-view printenv METRICS_API_KEY
```

2. Test nginx proxy:
```bash
curl -v http://localhost:3000/api/metrics
```

3. Test direct temper access:
```bash
export $(cat .env | grep METRICS_API_KEY)
curl -v -H "X-API-Key: $METRICS_API_KEY" http://localhost:3001/metrics
```

**Solutions:**

- **nginx env var not set:** Add to docker-compose.yml and rebuild
- **nginx not injecting header:** Check nginx.conf `proxy_set_header` directive
- **Key mismatch:** Ensure nginx and temper have same key
- **temper not validating correctly:** Check logs for errors

---

### Problem: llama-proxy says "API key not found"

**Symptoms:**
```
{"error":"API key not found or revoked"}
```

**Diagnosis:**

1. Check PostgreSQL for user's key:
```bash
docker exec -it ai-supabase-db-1 psql -U postgres -d postgres -c \
  "SELECT id, key_prefix, revoked FROM api_keys WHERE key = 'sk-ant-USER_KEY';"
```

2. Check llama-proxy logs:
```bash
docker compose logs llama-proxy | grep -i "api.*key"
```

3. Verify key is not revoked:
```sql
SELECT * FROM api_keys WHERE revoked = TRUE;
```

**Solutions:**

- **Key not in database:** User needs to create key via dashboard
- **Key revoked:** User needs to create new key
- **Database connection issue:** Check PostgreSQL is healthy
- **Cache issue:** Wait 60 seconds for cache to expire

---

### Problem: Healthcheck failing with auth error

**Symptoms:**
```bash
docker compose ps
# llama-server shows "unhealthy"
```

**Diagnosis:**

1. Check healthcheck command:
```bash
docker compose exec llama-server sh -c \
  'curl -f -H "Authorization: Bearer $LLAMA_API_KEY" http://localhost:8082/chat/health'
```

2. View healthcheck logs:
```bash
docker inspect llama-server --format='{{.State.Health.Log}}'
```

**Solutions:**

- **LLAMA_API_KEY not available in healthcheck:** Ensure env var is passed correctly
- **Healthcheck hitting wrong endpoint:** Update docker-compose.yml healthcheck command
- **Server not ready:** Increase healthcheck initial delay

---

## Key Rotation

### Rotating LLAMA_API_KEY

**Impact:** High - affects all internal monitoring and healthchecks

**Steps:**

1. Generate new key:
```bash
NEW_KEY="sk-ant-$(openssl rand -base64 32)"
echo "New LLAMA_API_KEY: $NEW_KEY"
```

2. Update .env:
```bash
sed -i.bak "s/^LLAMA_API_KEY=.*/LLAMA_API_KEY=$NEW_KEY/" .env
```

3. Restart affected services:
```bash
docker compose restart llama-server fan-manager
```

4. Verify health:
```bash
docker compose ps  # All should show healthy
curl -H "Authorization: Bearer $NEW_KEY" http://localhost:8082/chat/health
```

5. Monitor logs:
```bash
docker compose logs -f fan-manager llama-server
```

**Rollback:** Restore .env.bak and restart services

---

### Rotating METRICS_API_KEY

**Impact:** Medium - affects frontend metrics display

**Steps:**

1. Generate new key:
```bash
NEW_KEY="$(openssl rand -base64 32)"
echo "New METRICS_API_KEY: $NEW_KEY"
```

2. Update .env:
```bash
sed -i.bak "s/^METRICS_API_KEY=.*/METRICS_API_KEY=$NEW_KEY/" .env
```

3. Restart affected services:
```bash
docker compose restart fan-manager temper-view
```

4. Verify:
```bash
curl http://localhost:3000/api/metrics | jq .
```

5. Test direct access:
```bash
curl -H "X-API-Key: $NEW_KEY" http://localhost:3001/metrics | jq .
```

**Rollback:** Restore .env.bak and restart services

---

### Rotating User API Keys

**Impact:** Low - affects individual users only

**User Self-Service:**

1. User logs into temper-view dashboard
2. Goes to Settings → API Keys
3. Clicks "Revoke" on old key
4. Clicks "Create New Key"
5. Copies new key and updates applications

**Admin-Initiated:**

```sql
-- Revoke specific key
UPDATE api_keys SET revoked = TRUE WHERE id = 'key-uuid';

-- Revoke all keys for user
UPDATE api_keys SET revoked = TRUE WHERE user_id = 'user-uuid';
```

**Grace Period Pattern:**

Allow old keys to work for transition period:

```python
# llama-proxy
if is_key_revoked_recently(key, grace_period_hours=24):
    logger.warning(f"Using deprecated key {key_prefix}, revoked recently")
    # Still allow, but log warning
else:
    raise AuthenticationError("API key revoked")
```

---

## Emergency Key Revocation

### Suspected LLAMA_API_KEY Compromise

**Immediate Actions:**

1. **Rotate key immediately:**
```bash
NEW_KEY="sk-ant-$(openssl rand -base64 32)"
sed -i "s/^LLAMA_API_KEY=.*/LLAMA_API_KEY=$NEW_KEY/" .env
docker compose restart llama-server fan-manager
```

2. **Check access logs:**
```bash
docker compose logs llama-server | grep -i "401\|authentication" > auth_audit.log
```

3. **Look for suspicious patterns:**
- High request rate from unknown IPs
- Failed authentication attempts
- Unusual endpoint access patterns

4. **Consider network isolation:**
```bash
# Temporarily block external access
docker compose down
# Edit docker-compose.yml to bind llama-server to 127.0.0.1 only
docker compose up -d
```

---

### Suspected METRICS_API_KEY Compromise

**Lower Severity** (read-only access to metrics)

1. **Rotate key:**
```bash
NEW_KEY="$(openssl rand -base64 32)"
sed -i "s/^METRICS_API_KEY=.*/METRICS_API_KEY=$NEW_KEY/" .env
docker compose restart fan-manager temper-view
```

2. **Audit access:**
```bash
docker compose logs fan-manager | grep -i "401\|unauthorized"
```

3. **No immediate service disruption** (metrics are non-critical)

---

## See Also

- [API-REFERENCE.md](./API-REFERENCE.md) - Complete API documentation
- [STATUS-STATES.md](./STATUS-STATES.md) - AI service status states
- [TESTING-PLAYBOOK.md](./TESTING-PLAYBOOK.md) - Testing authentication flows
- [../CLAUDE.md](../CLAUDE.md) - System overview and development guide
