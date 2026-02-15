# AGENTS.md - AI Stack Operator's Manual

**Version:** 1.0
**Last Updated:** 2026-01-29
**Purpose:** Comprehensive operational procedures for autonomous agents and human administrators

This document defines rules, protocols, and procedures for modifying, testing, deploying, and maintaining the ai-stack platform. All agents and administrators must follow these guidelines to ensure system stability, security, and consistency.

---

## Table of Contents

1. [Change Management](#1-change-management)
2. [Testing Protocols](#2-testing-protocols)
3. [Git Workflow](#3-git-workflow)
4. [Network Topology & Access Control](#4-network-topology--access-control)
5. [Credential Management](#5-credential-management)
6. [Logging & Debugging Infrastructure](#6-logging--debugging-infrastructure)
7. [Security Procedures](#7-security-procedures)
8. [Deployment Procedures](#8-deployment-procedures)
9. [Monitoring & Alerting](#9-monitoring--alerting)
10. [Troubleshooting Reference](#10-troubleshooting-reference)
11. [Agent-Specific Guidelines](#11-agent-specific-guidelines)

---

## 1. Change Management

### 1.1 Modification Approval Matrix

| Change Type | Approval Required | Testing Required | Backup Required |
|-------------|-------------------|------------------|-----------------|
| **Configuration changes** (.env, models.ini, docker-compose.yml) | Human approval | Component test | Yes |
| **Code changes** (C++, Python, TypeScript) | Human approval | Unit + integration | Yes |
| **Security fixes** (from SecurityAudit.md) | Immediate with human notification | Full security test | Yes |
| **Dependency updates** | Human approval | Full regression test | Yes |
| **Documentation updates** | Agent autonomous | N/A | Optional |
| **Log rotation/cleanup** | Agent autonomous | N/A | No |

### 1.2 Change Implementation Process

**MANDATORY SEQUENCE:**

1. **Pre-change Assessment**
   ```bash
   # Check current system state
   docker compose ps
   docker compose logs --tail=50 <service-name>
   git status
   ```

2. **Create Backup**
   ```bash
   # For configuration changes
   cp docker-compose.yml docker-compose.yml.bak
   cp .env .env.bak

   # For volume data (use update.sh)
   ./update.sh --check-only  # Verifies backup capability
   ```

3. **Implement Change**
   - Make modifications to code/configuration
   - Document changes in commit message (see Git Workflow)
   - Update relevant documentation if needed

4. **Test Change** (see Testing Protocols)
   - Run component-specific tests
   - Verify no regressions in dependent services

5. **Deploy Change**
   ```bash
   # Rebuild specific service
   docker compose up -d --build <service-name>

   # Verify deployment
   docker compose ps <service-name>
   docker compose logs -f <service-name>
   ```

6. **Validation**
   - Confirm service health endpoints respond
   - Check logs for errors
   - Run smoke tests (see Testing Protocols)

7. **Rollback Procedure** (if needed)
   ```bash
   # Restore configuration
   cp docker-compose.yml.bak docker-compose.yml
   docker compose up -d --build <service-name>

   # Restore volume data
   ./update.sh --restore
   ```

### 1.3 Emergency Changes

For **CRITICAL** or **HIGH** severity security issues (see SecurityAudit.md):

1. Implement fix immediately
2. Test in isolated environment if possible
3. Deploy to production
4. Notify human administrator via commit message and logs
5. Document in SecurityAudit.md

---

## 2. Testing Protocols

### 2.1 Component Test Requirements

**Before ANY code deployment**, run the appropriate test suite:

#### temper (C++ GPU Control)
```bash
cd temper

# Build test
make clean && make

# Runtime test
./build/temper --help
./build/temper fanctl 50:30 70:65  # Test fan curve parsing

# Integration test (requires GPU)
docker compose up -d --build fan-manager
sleep 5
curl -s -H "X-API-Key: $METRICS_API_KEY" http://localhost:3001/metrics | jq .gpus[0].temperature
```

**Expected Results:**
- Clean compilation with no warnings
- Help text displays correctly
- Fan curve parsing succeeds
- Metrics endpoint returns valid JSON with GPU data

#### ai-proxy (Python Gateway)
```bash
cd ai-proxy

# Check Python syntax
python3 -m py_compile proxy.py

# Integration test (requires running services)
docker compose up -d ai-proxy
sleep 5

# Test health endpoint
curl -X POST http://localhost:8081/v1/chat/completions \
  -H "Authorization: Bearer sk-ant-test123" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-3-5-sonnet-20241022","messages":[{"role":"user","content":"test"}],"max_tokens":5}'

# Check logs
docker compose logs ai-proxy | tail -20
```

**Expected Results:**
- No Python syntax errors
- Service starts without errors
- Logs show API key validation attempt
- Response indicates auth success/failure (depending on key validity)

#### temper-view (React Frontend)
```bash
cd temper-view

# Install dependencies (first time only)
npm install

# Type check
npm run type-check  # If available, otherwise:
npx tsc --noEmit

# Build test
npm run build

# Verify build output
ls -lh dist/
```

**Expected Results:**
- No TypeScript errors
- Build completes successfully
- `dist/` directory contains compiled assets
- No console errors about missing dependencies

#### llama.cpp (Inference Engine)
```bash
# Health check
curl -H "Authorization: Bearer $LLAMA_API_KEY" \
  http://localhost:8082/chat/health

# Model loading check
docker compose logs llama-server | grep -i "model"
```

**Expected Results:**
- Health endpoint returns 200 OK
- Logs show successful model loading
- No CUDA errors or OOM messages

### 2.2 Integration Test Suite

**Run after ANY change that affects multiple services:**

```bash
#!/bin/bash
# integration-test.sh - Run full stack integration tests

echo "=== AI Stack Integration Test Suite ==="

# Test 1: Service Health
echo "Test 1: Checking all services are running..."
docker compose ps | grep -v "Exit" || exit 1

# Test 2: Database Connectivity
echo "Test 2: Testing database connection..."
docker exec -it ai-supabase-db-1 psql -U postgres -d postgres -c "SELECT 1;" || exit 1

# Test 3: GPU Metrics
echo "Test 3: Fetching GPU metrics..."
curl -s -H "X-API-Key: $METRICS_API_KEY" http://localhost:3001/metrics | jq -e '.gpus[0]' || exit 1

# Test 4: LLM Inference
echo "Test 4: Testing LLM inference..."
curl -s -X POST http://localhost:8081/v1/chat/completions \
  -H "Authorization: Bearer $LLAMA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-3-5-sonnet-20241022","messages":[{"role":"user","content":"Respond with only the word SUCCESS"}],"max_tokens":10}' \
  | jq -e '.choices[0].message.content' || exit 1

# Test 5: Frontend Accessibility
echo "Test 5: Testing frontend..."
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000 | grep -q "200" || exit 1

echo "=== All tests passed ==="
```

**Save this script to `/home/tyler/ai-stack/integration-test.sh` and run:**
```bash
chmod +x integration-test.sh
./integration-test.sh
```

### 2.3 Security Testing

After implementing fixes from `SecurityAudit.md`:

```bash
# Test 1: API Key Validation
curl -s -o /dev/null -w "%{http_code}" http://localhost:3001/metrics
# Expected: 401 or 403 (unauthorized)

curl -s -o /dev/null -w "%{http_code}" \
  -H "X-API-Key: invalid_key" http://localhost:3001/metrics
# Expected: 401 or 403

curl -s -o /dev/null -w "%{http_code}" \
  -H "X-API-Key: $METRICS_API_KEY" http://localhost:3001/metrics
# Expected: 200

# Test 2: CORS Restrictions
curl -s -H "Origin: https://evil.com" \
  -H "X-API-Key: $METRICS_API_KEY" \
  http://localhost:3001/metrics -v 2>&1 | grep -i "access-control-allow-origin"
# Expected: Should NOT return "Access-Control-Allow-Origin: *"

# Test 3: IPMI Command Injection (safe test)
# This should be rejected by input validation
docker exec fan-manager /app/build/temper fanctl "50:30; echo pwned"
# Expected: Error or rejection, NOT command execution
```

### 2.4 Performance Testing

```bash
# GPU Load Test
for i in {1..10}; do
  curl -X POST http://localhost:8081/v1/chat/completions \
    -H "Authorization: Bearer sk-ant-valid-key" \
    -H "Content-Type: application/json" \
    -d '{"model":"claude-3-5-sonnet-20241022","messages":[{"role":"user","content":"Count from 1 to 100"}],"max_tokens":500}' &
done
wait

# Check GPU metrics during load
curl -s -H "X-API-Key: $METRICS_API_KEY" http://localhost:3001/metrics \
  | jq '.gpus[] | {temp: .temperature, util: .utilization, power: .power_watts}'
```

---

## 3. Git Workflow

### 3.1 Repository Structure

```
ai-stack/                 # Main repository
├── .git/                 # Git repository
├── llama.cpp/            # Submodule (upstream llama.cpp)
├── temper/               # Submodule (GPU control)
├── temper-view/          # Submodule (frontend)
├── ai-proxy/          # Local component
├── stripe-handler/       # Local component
└── supabase-ai/          # Local component
```

**Submodule Management:**
```bash
# Initialize submodules (first time)
git submodule update --init --recursive

# Update all submodules to latest
git submodule update --remote --merge

# Check submodule status
git submodule status
```

### 3.2 Branch Strategy

**RULE: Do NOT use feature branches unless explicitly requested by human administrator.**

All changes go directly to `master` branch with appropriate commit messages and testing.

If feature branch IS required:
```bash
# Create feature branch
git checkout -b feature/description

# Make changes, test, commit

# Merge back to master (with human approval)
git checkout master
git merge --no-ff feature/description
git branch -d feature/description
```

### 3.3 Commit Standards

**MANDATORY commit message format:**

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Types:**
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code formatting (no logic change)
- `refactor`: Code restructuring (no behavior change)
- `perf`: Performance improvement
- `test`: Adding/modifying tests
- `chore`: Maintenance tasks
- `security`: Security fixes

**Scopes:**
- `temper`: C++ GPU control
- `proxy`: ai-proxy Python service
- `frontend`: temper-view React app
- `llama`: llama.cpp inference engine
- `stripe`: Billing service
- `supabase`: Database/auth
- `docker`: Docker/compose configuration
- `docs`: Documentation

**Examples:**

```
feat(proxy): add request rate limiting to prevent DoS

Implements token bucket rate limiting with 100 req/min limit
per API key. Addresses SecurityAudit.md issue #8.

Tested with concurrent curl requests, verified 429 responses
after threshold.

Closes #8 from SecurityAudit.md
```

```
fix(temper): sanitize IPMI command arguments to prevent injection

Added input validation for host, user, pass parameters.
Only alphanumeric, dot, hyphen, underscore characters allowed.
Addresses HIGH severity issue #3 from SecurityAudit.md.

Tested with malicious inputs: `host; rm -rf /`, verified rejection.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
```

### 3.4 Commit Process (Agent-Executed)

**When asked to commit changes:**

1. **Stage specific files only** (never `git add -A` or `git add .`)
   ```bash
   git status
   git add docker-compose.yml
   git add ai-proxy/proxy.py
   ```

2. **Write descriptive commit message**
   ```bash
   git commit -m "$(cat <<'EOF'
   fix(proxy): validate API keys against database instead of cache only

   Updated API key validation to always check database for revoked keys.
   Cache now has 30-second TTL instead of 60 seconds.

   Tested with:
   - Valid active key: SUCCESS
   - Revoked key: 401 Unauthorized
   - Invalid key: 401 Unauthorized

   Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
   EOF
   )"
   ```

3. **Verify commit**
   ```bash
   git log -1 --stat
   ```

4. **Do NOT push unless explicitly requested**

### 3.5 Viewing History

```bash
# View recent commits
git log --oneline -10

# View commits for specific component
git log --oneline --grep="proxy"

# View commits by date
git log --since="2026-01-20" --until="2026-01-29"

# View file history
git log --follow -- docker-compose.yml
```

---

## 4. Network Topology & Access Control

### 4.1 Network Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        PUBLIC INTERNET                       │
└────────────────────────────┬────────────────────────────────┘
                             │
                   ┌─────────▼─────────┐
                   │   Port 3000 (HTTP)│
                   │   temper-view     │
                   │   (Nginx)         │
                   └─────────┬─────────┘
                             │
        ┏━━━━━━━━━━━━━━━━━━━━┻━━━━━━━━━━━━━━━━━━━━━┓
        ┃         Docker Network: ai-stack-net      ┃
        ┃              (External Bridge)            ┃
        ┗━━━━━━━━━━━━━┯━━━━━━━━━━━━━━┯━━━━━━━━━━━━┛
                      │              │
         ┌────────────▼──────────┐   │   ┌────────────────────┐
         │   Port 8004 (HTTP)    │   │   │  Port 8081 (HTTP)  │
         │   Supabase Kong       │   │   │  ai-proxy       │
         │   (API Gateway)       │   │   │                    │
         └────────────┬──────────┘   │   └────────┬───────────┘
                      │              │            │
         ┌────────────▼──────────┐   │   ┌────────▼───────────┐
         │   Port 5433 (PG)      │   │   │  Port 8082 (HTTP)  │
         │   PostgreSQL DB       │   │   │  llama-server      │
         │                       │   │   │  (Internal Only)   │
         └───────────────────────┘   │   └────────────────────┘
                                     │
                        ┌────────────▼──────────┐
                        │  Port 3001 (HTTP)     │
                        │  fan-manager (temper) │
                        │  (127.0.0.1 only)     │
                        └───────────────────────┘
                                     │
                        ┌────────────▼──────────┐
                        │  GPU Hardware (NVML)  │
                        │  iDRAC (10.20.20.3)   │
                        └───────────────────────┘
```

### 4.2 Port Accessibility Matrix

| Service | Port | External | Internal | Localhost | Auth Required |
|---------|------|----------|----------|-----------|---------------|
| **temper-view** | 3000 | ✅ HTTP | ✅ | ✅ | Via Supabase JWT |
| **ai-proxy** | 8081 | ✅ HTTP | ✅ | ✅ | API Key (database) |
| **llama-server** | 8082 | ❌ | ✅ | ✅ | LLAMA_API_KEY |
| **fan-manager** | 3001 | ❌ | ❌ | ✅ | METRICS_API_KEY |
| **Supabase Kong** | 8004 | ✅ HTTP | ✅ | ✅ | JWT tokens |
| **Supabase Studio** | 8003 | ⚠️ DEV ONLY | ✅ | ✅ | supabase/supabase |
| **PostgreSQL** | 5433 | ❌ | ✅ | ✅ | Password |
| **stripe-handler** | N/A | Via webhooks | ✅ | ✅ | Webhook secret |

**Legend:**
- ✅ = Accessible
- ❌ = Not accessible (blocked by firewall/binding)
- ⚠️ = Should be blocked in production

### 4.3 Firewall Rules (Production)

**MANDATORY firewall configuration for production deployment:**

```bash
# Allow HTTPS (443) from anywhere
sudo ufw allow 443/tcp

# Allow HTTP (80) from anywhere (for Let's Encrypt)
sudo ufw allow 80/tcp

# Allow SSH (22) from trusted IPs only
sudo ufw allow from 192.168.1.0/24 to any port 22

# DENY direct access to service ports
sudo ufw deny 3001/tcp  # fan-manager
sudo ufw deny 8081/tcp  # ai-proxy (should be behind reverse proxy)
sudo ufw deny 8082/tcp  # llama-server
sudo ufw deny 8003/tcp  # Supabase Studio (dev only)
sudo ufw deny 8004/tcp  # Supabase Kong (should be behind reverse proxy)
sudo ufw deny 5433/tcp  # PostgreSQL

# Enable firewall
sudo ufw enable
```

**For development environment, keep ports open but behind authentication.**

### 4.4 Internal Service Communication

**Authentication between services:**

| From | To | Auth Method | Credential Location |
|------|-----|-------------|---------------------|
| ai-proxy | llama-server | Bearer token | `LLAMA_API_KEY` in .env |
| ai-proxy | PostgreSQL | Password | `POSTGRES_PASSWORD` in .env |
| fan-manager | llama-server | Bearer token | `LLAMA_API_KEY` in .env |
| fan-manager | iDRAC | Password | `IDRAC_USER`/`IDRAC_PASS` in .env |
| temper-view | fan-manager | API key | `METRICS_API_KEY` in .env |
| temper-view | Supabase | JWT + Anon key | Runtime (browser) + supabase.ts |
| stripe-handler | PostgreSQL | Password | `POSTGRES_PASSWORD` in .env |
| stripe-handler | Stripe API | Secret key | `STRIPE_SECRET_KEY` in .env |

### 4.5 Network Troubleshooting

```bash
# Test service connectivity from within network
docker exec ai-proxy curl -s http://llama-server:8082/chat/health

# Test external connectivity
curl -v http://localhost:3000

# Check network configuration
docker network inspect ai-stack-net

# View active connections
docker compose ps
netstat -tuln | grep -E "3000|3001|8081|8082|8003|8004|5433"

# Test DNS resolution within Docker network
docker exec ai-proxy ping -c 1 llama-server
docker exec ai-proxy ping -c 1 db
```

---

## 5. Credential Management

### 5.1 Credential Storage Hierarchy

**NEVER commit credentials to git. All sensitive data MUST be in `.env` (gitignored).**

**Credential Types:**

1. **System Credentials** (.env)
   - `LLAMA_API_KEY` - Internal API authentication
   - `POSTGRES_PASSWORD` - Database password
   - `METRICS_API_KEY` - Metrics endpoint authentication

2. **Third-Party Credentials** (.env)
   - `STRIPE_SECRET_KEY` - Stripe API key
   - `STRIPE_WEBHOOK_SECRET` - Webhook validation
   - `IDRAC_USER` / `IDRAC_PASS` - iDRAC hardware access

3. **Supabase Credentials** (supabase-credentials.md + .env)
   - Studio login: `supabase` / `supabase` (dev only)
   - Postgres password: In .env
   - JWT secret: In .env
   - Anon key: In .env (safe for frontend)
   - Service role key: In .env (NEVER expose to frontend)

4. **User Credentials** (PostgreSQL database)
   - User API keys: `api_keys` table
   - User passwords: Supabase Auth (hashed)
   - Stripe customer IDs: `profiles` table

### 5.2 Agent Access to Credentials

**Agents have READ-ONLY access to credentials via environment variables:**

```bash
# Reading credentials (allowed)
echo $LLAMA_API_KEY
cat .env | grep METRICS_API_KEY

# Writing/modifying credentials (NOT ALLOWED without human approval)
# sed -i 's/LLAMA_API_KEY=.*/LLAMA_API_KEY=new_key/' .env  # ❌ FORBIDDEN
```

**RULE: Agents MUST NOT modify .env or credentials without explicit human approval.**

### 5.3 Credential Rotation Procedure

**When rotating credentials (requires human approval):**

1. **Generate new credential**
   ```bash
   # Example: Generate new LLAMA_API_KEY
   NEW_KEY="sk-ant-$(openssl rand -hex 32)"
   echo "New LLAMA_API_KEY: $NEW_KEY"
   ```

2. **Update .env**
   ```bash
   # Backup current .env
   cp .env .env.backup

   # Update credential
   sed -i "s/^LLAMA_API_KEY=.*/LLAMA_API_KEY=$NEW_KEY/" .env
   ```

3. **Restart affected services**
   ```bash
   # Restart services that use the credential
   docker compose up -d --force-recreate ai-proxy fan-manager
   ```

4. **Verify new credential works**
   ```bash
   curl -H "Authorization: Bearer $NEW_KEY" \
     http://localhost:8082/chat/health
   ```

5. **Remove backup once verified**
   ```bash
   rm .env.backup
   ```

### 5.4 Human Administrator Credentials

**Access Points:**

1. **Supabase Studio** (Database Management)
   - URL: http://localhost:8003
   - Login: `supabase` / `supabase`
   - Capabilities: View tables, run SQL queries, manage auth users

2. **PostgreSQL Direct Access**
   ```bash
   docker exec -it ai-supabase-db-1 psql -U postgres -d postgres
   ```

3. **SSH Access** (Production server)
   - User: `tyler` (or configured admin user)
   - Auth: SSH key-based authentication
   - sudo capabilities for Docker management

4. **Stripe Dashboard** (Billing Management)
   - URL: https://dashboard.stripe.com
   - Account: Configured in STRIPE_SECRET_KEY
   - Capabilities: View subscriptions, invoices, webhooks

### 5.5 Credential Security Audit

**Run this check before any deployment:**

```bash
#!/bin/bash
# credential-audit.sh - Check for credential leaks

echo "=== Credential Security Audit ==="

# Check for .env in git
if git ls-files | grep -q "^\.env$"; then
  echo "❌ ERROR: .env is tracked in git!"
  exit 1
fi

# Check for credentials in code
grep -r "sk-ant-api" --include="*.py" --include="*.ts" --include="*.tsx" --include="*.cpp" .
if [ $? -eq 0 ]; then
  echo "⚠️  WARNING: Found hardcoded credentials in code"
fi

# Check .env permissions
if [ -f .env ]; then
  PERMS=$(stat -c %a .env)
  if [ "$PERMS" != "600" ]; then
    echo "⚠️  WARNING: .env permissions are $PERMS (should be 600)"
    chmod 600 .env
  fi
fi

# Check for Supabase keys in frontend
grep -r "eyJhbGciOi" temper-view/src/ 2>/dev/null
if [ $? -eq 0 ]; then
  echo "⚠️  WARNING: Hardcoded Supabase keys found (see SecurityAudit.md #1)"
fi

echo "=== Audit Complete ==="
```

---

## 6. Logging & Debugging Infrastructure

### 6.1 Log Locations

| Service | Log Location | Format | Rotation | Retention |
|---------|--------------|--------|----------|-----------|
| **ai-proxy** | `/home/tyler/ai-stack/ai-proxy/logs/*.log` | Plain text | Manual | 30 days |
| **fan-manager** | Docker logs | stdout/stderr | Docker | 7 days |
| **llama-server** | Docker logs | stdout/stderr | Docker | 7 days |
| **temper-view** | Docker logs (Nginx) | Combined log format | Docker | 7 days |
| **stripe-handler** | Docker logs | stdout/stderr | Docker | 7 days |
| **Supabase** | `/home/tyler/ai-stack/supabase-ai/volumes/logs/*` | Various | Supabase | 7 days |
| **PostgreSQL** | Supabase logs + Docker | Postgres format | Supabase | 7 days |

### 6.2 Log Access Commands

```bash
# View live logs (all services)
docker compose logs -f

# View logs for specific service
docker compose logs -f ai-proxy
docker compose logs -f fan-manager

# View logs with timestamp
docker compose logs -f --timestamps llama-server

# View last N lines
docker compose logs --tail=100 llama-server

# Search logs for errors
docker compose logs llama-server | grep -i error
docker compose logs fan-manager | grep -i "nvml"

# Export logs to file for analysis
docker compose logs ai-proxy > /tmp/ai-proxy-debug.log

# ai-proxy persistent logs
tail -f ai-proxy/logs/*.log
ls -lh ai-proxy/logs/
```

### 6.3 Debug Mode Activation

**Enable verbose logging for troubleshooting:**

```bash
# fan-manager (already enabled via VERBOSE=1 in docker-compose.yml)
# To change, edit docker-compose.yml:
# environment:
#   - VERBOSE=1  # 0=normal, 1=verbose, 2=debug

docker compose up -d --force-recreate fan-manager

# ai-proxy (edit proxy.py to add logging)
# Check current log level in proxy.py

# llama-server (add --verbose flag)
# Edit docker-compose.yml command section:
# command: >
#   --host 0.0.0.0
#   --port 8082
#   --verbose
#   ...

docker compose up -d --force-recreate llama-server
```

### 6.4 Scratchpad Directory (Agent Debugging)

**For autonomous agents (Claude Code), use dedicated scratchpad:**

```bash
# Scratchpad location (auto-created per session)
SCRATCHPAD="/tmp/claude/-home-tyler-ai-stack/<session-id>/scratchpad"

# Usage examples (agents only):
# - Save debug output
curl -s http://localhost:3001/metrics > $SCRATCHPAD/metrics-debug.json

# - Save test results
./integration-test.sh > $SCRATCHPAD/test-results.log 2>&1

# - Temporary script execution
echo "#!/bin/bash" > $SCRATCHPAD/test.sh
chmod +x $SCRATCHPAD/test.sh

# RULE: Never use /tmp directly, always use $SCRATCHPAD for agent operations
```

### 6.5 Log Rotation Policy

**Manual log rotation for ai-proxy:**

```bash
#!/bin/bash
# rotate-logs.sh - Rotate ai-proxy logs

LOG_DIR="/home/tyler/ai-stack/ai-proxy/logs"
RETENTION_DAYS=30

# Archive old logs
find $LOG_DIR -name "*.log" -type f -mtime +7 -exec gzip {} \;

# Delete archived logs older than retention period
find $LOG_DIR -name "*.log.gz" -type f -mtime +$RETENTION_DAYS -delete

echo "Log rotation complete"
```

**Add to crontab (human administrator):**
```bash
# Run daily at 2 AM
0 2 * * * /home/tyler/ai-stack/rotate-logs.sh
```

### 6.6 Debugging Checklist

**When investigating issues, follow this sequence:**

1. **Check service status**
   ```bash
   docker compose ps
   docker compose top
   ```

2. **Review recent logs**
   ```bash
   docker compose logs --tail=200 --timestamps
   ```

3. **Check resource utilization**
   ```bash
   docker stats --no-stream
   nvidia-smi
   df -h
   free -h
   ```

4. **Verify network connectivity**
   ```bash
   docker network inspect ai-stack-net
   docker exec ai-proxy ping -c 1 llama-server
   ```

5. **Check configuration**
   ```bash
   cat docker-compose.yml | grep -A 5 <service-name>
   cat .env | grep <VARIABLE>
   ```

6. **Test endpoints**
   ```bash
   curl -v http://localhost:3000
   curl -H "Authorization: Bearer $LLAMA_API_KEY" http://localhost:8082/chat/health
   ```

7. **Database queries**
   ```bash
   docker exec -it ai-supabase-db-1 psql -U postgres -d postgres \
     -c "SELECT * FROM api_keys ORDER BY created_at DESC LIMIT 5;"
   ```

---

## 7. Security Procedures

### 7.1 Security Audit Compliance

**MANDATORY: Review SecurityAudit.md before ANY deployment.**

**Current Outstanding Issues (as of 2026-01-29):**

- 🔴 **CRITICAL**: Hardcoded Supabase anon key (issue #1)
- 🔴 **CRITICAL**: API key validation needs database backend (issue #2)
- 🟠 **HIGH**: IPMI command injection risk (issue #3)
- 🟠 **HIGH**: CORS restrictions needed (issue #4)
- 🟠 **HIGH**: CSP headers missing (issue #5)

**Before deploying to production, ALL CRITICAL and HIGH issues MUST be resolved.**

### 7.2 Security Testing Protocol

```bash
#!/bin/bash
# security-test.sh - Run security validation tests

echo "=== Security Test Suite ==="

# Test 1: Unauthorized access blocked
echo "Test 1: Unauthorized access..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3001/metrics)
if [ "$HTTP_CODE" != "401" ] && [ "$HTTP_CODE" != "403" ]; then
  echo "❌ FAIL: Metrics endpoint accessible without auth (got $HTTP_CODE)"
  exit 1
fi
echo "✅ PASS"

# Test 2: Valid auth succeeds
echo "Test 2: Valid authentication..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  -H "X-API-Key: $METRICS_API_KEY" http://localhost:3001/metrics)
if [ "$HTTP_CODE" != "200" ]; then
  echo "❌ FAIL: Valid auth rejected (got $HTTP_CODE)"
  exit 1
fi
echo "✅ PASS"

# Test 3: SQL injection attempt
echo "Test 3: SQL injection protection..."
# This should NOT crash the service or return SQL errors
curl -s -X POST http://localhost:8081/v1/chat/completions \
  -H "Authorization: Bearer sk-ant-'; DROP TABLE api_keys; --" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-3-5-sonnet-20241022","messages":[{"role":"user","content":"test"}]}' \
  > /dev/null
# Check service is still running
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $LLAMA_API_KEY" http://localhost:8082/chat/health)
if [ "$HTTP_CODE" != "200" ]; then
  echo "❌ FAIL: Service crashed or unhealthy after injection attempt"
  exit 1
fi
echo "✅ PASS"

# Test 4: Verify .env not in git
echo "Test 4: Credential leak check..."
if git ls-files | grep -q "^\.env$"; then
  echo "❌ FAIL: .env tracked in git"
  exit 1
fi
echo "✅ PASS"

echo "=== Security tests passed ==="
```

### 7.3 Incident Response Procedure

**If security breach detected:**

1. **Immediate Actions**
   ```bash
   # Stop all services
   docker compose down

   # Rotate ALL credentials
   cp .env .env.compromised
   # Generate new credentials (see section 5.3)

   # Check logs for unauthorized access
   docker compose logs | grep -i "401\|403\|unauthorized" > /tmp/security-incident.log
   ```

2. **Investigation**
   - Review access logs for suspicious IPs
   - Check database for unauthorized API keys
   - Review git history for exposed credentials
   - Analyze network traffic logs (if available)

3. **Remediation**
   - Update all credentials
   - Revoke compromised API keys in database
   - Apply security patches from SecurityAudit.md
   - Restart services with new configuration

4. **Notification**
   - Document incident in git commit
   - Notify human administrator
   - Update SecurityAudit.md with new findings

### 7.4 Periodic Security Checks

**Run monthly (automated via cron):**

```bash
#!/bin/bash
# monthly-security-check.sh

echo "=== Monthly Security Check $(date) ===" | tee -a /var/log/security-checks.log

# Check for outdated Docker images
docker images | grep "days ago\|weeks ago\|months ago" | tee -a /var/log/security-checks.log

# Check for known vulnerabilities in Python dependencies
docker exec ai-proxy pip list --outdated | tee -a /var/log/security-checks.log

# Check file permissions
find /home/tyler/ai-stack -name "*.env" -exec stat -c "%a %n" {} \; | tee -a /var/log/security-checks.log

# Check for exposed ports
netstat -tuln | grep LISTEN | tee -a /var/log/security-checks.log

echo "=== Check Complete ===" | tee -a /var/log/security-checks.log
```

---

## 8. Deployment Procedures

### 8.1 Initial Deployment (Fresh Install)

```bash
# 1. Clone repository
git clone <repository-url> ai-stack
cd ai-stack

# 2. Initialize submodules
git submodule update --init --recursive

# 3. Create .env from template
cat > .env <<EOF
LLAMA_API_KEY=sk-ant-$(openssl rand -hex 32)
METRICS_API_KEY=$(openssl rand -hex 32)
POSTGRES_PASSWORD=$(openssl rand -base64 32)
POSTGRES_DB=postgres
STRIPE_SECRET_KEY=sk_test_YOUR_KEY
STRIPE_WEBHOOK_SECRET=whsec_YOUR_SECRET
IDRAC_IP=10.20.20.3
IDRAC_USER=temper
IDRAC_PASS=YOUR_IDRAC_PASSWORD
EOF

chmod 600 .env

# 4. Create Docker network
docker network create ai-stack-net

# 5. Build and start services
docker compose up -d --build

# 6. Wait for initialization
sleep 60

# 7. Run integration tests
./integration-test.sh

# 8. Initialize database schema (if needed)
# See supabase-ai documentation
```

### 8.2 Update Deployment (Existing Installation)

```bash
# 1. Backup current state
./update.sh --check-only
docker compose down

# 2. Pull latest code
git pull origin master
git submodule update --remote --merge

# 3. Rebuild affected services
docker compose up -d --build

# 4. Verify deployment
docker compose ps
./integration-test.sh

# 5. Check logs for errors
docker compose logs --tail=50
```

### 8.3 Rollback Procedure

```bash
# 1. Stop services
docker compose down

# 2. Restore previous git state
git log --oneline -5  # Identify commit to restore
git checkout <previous-commit-hash>
git submodule update --recursive

# 3. Restore volumes if needed
./update.sh --restore

# 4. Restart services
docker compose up -d

# 5. Verify rollback success
docker compose ps
./integration-test.sh
```

### 8.4 Blue-Green Deployment (Advanced)

**For zero-downtime updates:**

```bash
# 1. Clone current deployment
cp docker-compose.yml docker-compose-blue.yml

# 2. Create green deployment with different ports
sed 's/3000:80/3001:80/' docker-compose.yml > docker-compose-green.yml
sed -i 's/8081:8081/8082:8081/' docker-compose-green.yml

# 3. Start green deployment
docker compose -f docker-compose-green.yml up -d

# 4. Test green deployment
curl http://localhost:3001

# 5. Switch traffic (update reverse proxy)
# Update Nginx/load balancer to point to green deployment

# 6. Stop blue deployment
docker compose -f docker-compose-blue.yml down
```

---

## 9. Monitoring & Alerting

### 9.1 Health Monitoring

**Critical health checks (run every 5 minutes):**

```bash
#!/bin/bash
# health-check.sh - Monitor system health

# Check GPU temperature
GPU_TEMP=$(curl -s -H "X-API-Key: $METRICS_API_KEY" http://localhost:3001/metrics \
  | jq -r '.gpus[0].temperature')

if [ "$GPU_TEMP" -gt 85 ]; then
  echo "ALERT: GPU temperature critical: ${GPU_TEMP}°C" | tee -a /var/log/alerts.log
  # Send alert (implement notification method)
fi

# Check service status
if ! docker compose ps | grep -q "Up"; then
  echo "ALERT: Service down" | tee -a /var/log/alerts.log
fi

# Check disk space
DISK_USAGE=$(df -h / | awk 'NR==2 {print $5}' | sed 's/%//')
if [ "$DISK_USAGE" -gt 90 ]; then
  echo "ALERT: Disk usage critical: ${DISK_USAGE}%" | tee -a /var/log/alerts.log
fi

# Check database connectivity
if ! docker exec ai-supabase-db-1 psql -U postgres -c "SELECT 1" > /dev/null 2>&1; then
  echo "ALERT: Database unreachable" | tee -a /var/log/alerts.log
fi
```

### 9.2 Performance Metrics

**Collect every 1 minute:**

```bash
# GPU metrics
curl -s -H "X-API-Key: $METRICS_API_KEY" http://localhost:3001/metrics \
  | jq '{
      gpus: [.gpus[] | {
        id, temperature, utilization, power_watts, memory_used_mb
      }],
      system: {
        cpu_temp: .system.cpu_temp_celsius,
        ram_used: .system.ram_used_mb,
        uptime: .system.uptime_seconds
      }
    }' \
  >> /var/log/metrics/$(date +%Y%m%d).json

# LLM inference metrics
curl -s -H "Authorization: Bearer $LLAMA_API_KEY" \
  http://localhost:8082/chat/metrics \
  | jq '{requests: .requests_total, tokens: .tokens_generated}' \
  >> /var/log/metrics/llm-$(date +%Y%m%d).json
```

### 9.3 Alert Thresholds

| Metric | Warning | Critical | Action |
|--------|---------|----------|--------|
| GPU Temp | >80°C | >85°C | Check fans, reduce power limit |
| GPU Util | >95% | 100% sustained | Consider load balancing |
| Disk Usage | >80% | >90% | Clean logs, prune Docker images |
| RAM Usage | >80% | >90% | Restart services, check for leaks |
| API Error Rate | >5% | >10% | Check logs, review recent changes |
| Response Time | >5s | >10s | Check GPU load, network latency |

### 9.4 Dashboard Access

**Real-time monitoring via temper-view:**

- URL: http://localhost:3000
- Login: Supabase Auth
- Metrics: GPU temperature, utilization, power, memory, fan speeds, API usage

**Database monitoring via Supabase Studio:**

- URL: http://localhost:8003
- Login: supabase/supabase
- View: API keys, user profiles, usage logs

---

## 10. Troubleshooting Reference

### 10.1 Common Issues & Solutions

#### Issue: "GPU not detected in temper"

**Symptoms:**
- fan-manager logs show "Failed to initialize NVML"
- No GPU data in metrics endpoint

**Diagnosis:**
```bash
# Test GPU access on host
nvidia-smi

# Check Docker GPU passthrough
docker run --rm --gpus all nvidia/cuda:13.1-base nvidia-smi

# Check container GPU access
docker exec fan-manager nvidia-smi
```

**Solutions:**
1. Install NVIDIA Container Toolkit: `sudo apt install nvidia-container-toolkit`
2. Restart Docker: `sudo systemctl restart docker`
3. Verify docker-compose.yml has GPU reservation configured
4. Rebuild fan-manager: `docker compose up -d --build fan-manager`

---

#### Issue: "ai-proxy returns 401 Unauthorized"

**Symptoms:**
- API requests fail with 401
- Logs show "API key validation failed"

**Diagnosis:**
```bash
# Check API key exists in database
docker exec -it ai-supabase-db-1 psql -U postgres -d postgres \
  -c "SELECT id, key_prefix, created_at FROM api_keys ORDER BY created_at DESC;"

# Check ai-proxy logs
docker compose logs ai-proxy | grep -i "api key"

# Test database connectivity from proxy
docker exec ai-proxy ping -c 1 db
```

**Solutions:**
1. Create API key in database (via temper-view UI or SQL)
2. Verify POSTGRES_PASSWORD in .env matches database
3. Check database is running: `docker compose ps db`
4. Restart ai-proxy: `docker compose restart ai-proxy`

---

#### Issue: "Model loading fails in llama-server"

**Symptoms:**
- llama-server crashes on startup
- Logs show "Failed to load model"
- CUDA out of memory errors

**Diagnosis:**
```bash
# Check available VRAM
nvidia-smi

# Check model file exists
docker exec llama-server ls -lh /models/

# Check models.ini configuration
cat models.ini

# Review startup logs
docker compose logs llama-server | grep -i "model\|error\|cuda"
```

**Solutions:**
1. Verify model files downloaded: `./update.sh`
2. Check tensor-split configuration in models.ini matches GPU VRAM
3. Ensure only one model loads: `models-max 1` in docker-compose.yml
4. Reduce context length in models.ini
5. Clear GPU memory: `docker compose restart llama-server`

---

#### Issue: "Frontend not loading (temper-view)"

**Symptoms:**
- Browser shows blank page or Nginx error
- http://localhost:3000 unreachable

**Diagnosis:**
```bash
# Check Nginx is running
docker compose ps temper-view

# Check Nginx logs
docker compose logs temper-view | tail -50

# Test direct Nginx access
curl -v http://localhost:3000

# Check if port is bound
netstat -tuln | grep 3000
```

**Solutions:**
1. Rebuild frontend: `docker compose up -d --build temper-view`
2. Check environment variables passed to container
3. Verify Nginx configuration in Dockerfile
4. Test backend connectivity: `curl http://localhost:3001/metrics`
5. Clear browser cache and reload

---

#### Issue: "Database connection refused"

**Symptoms:**
- Services can't connect to PostgreSQL
- "Connection refused" or "Connection timeout" errors

**Diagnosis:**
```bash
# Check database is running
docker compose ps db

# Check database logs
docker compose logs db | tail -50

# Test database connectivity
docker exec -it ai-supabase-db-1 psql -U postgres -c "SELECT 1"

# Verify network
docker network inspect ai-stack-net | grep db
```

**Solutions:**
1. Ensure database service is in `ai-stack-net` network
2. Check `POSTGRES_PASSWORD` in .env is correct
3. Restart database: `docker compose restart db`
4. Wait for database initialization (first start takes 60+ seconds)
5. Check disk space: `df -h`

---

#### Issue: "Fan control not working"

**Symptoms:**
- GPU fans not responding to temperature changes
- iDRAC chassis fans stuck at 100%

**Diagnosis:**
```bash
# Check fan-manager is running
docker compose ps fan-manager

# Check logs for IPMI errors
docker compose logs fan-manager | grep -i "ipmi\|fan"

# Test iDRAC connectivity
ping -c 1 $IDRAC_IP

# Check GPU fan control via NVML
docker exec fan-manager nvidia-smi -q | grep -i fan
```

**Solutions:**
1. Verify iDRAC credentials in .env
2. Enable IPMI over LAN in iDRAC settings
3. Check `FAN_SETPOINTS` format in docker-compose.yml
4. Ensure fan-manager runs privileged mode
5. Test manual fan control: `ipmitool -I lanplus -H $IDRAC_IP -U $IDRAC_USER -P $IDRAC_PASS raw 0x30 0x30 0x01 0x00`

---

### 10.2 Emergency Recovery

**If system is completely unresponsive:**

```bash
# 1. Stop all services
docker compose down -v  # WARNING: -v removes volumes

# 2. Restore from backup
./update.sh --restore

# 3. Clean Docker system
docker system prune -af
docker volume prune -f

# 4. Rebuild from scratch
docker compose up -d --build --force-recreate

# 5. Re-initialize database
# Follow Supabase initialization steps
```

**If data corruption suspected:**

```bash
# 1. Stop services
docker compose down

# 2. Backup current volumes
docker run --rm -v ai-stack_llama_cache:/data -v $(pwd)/backups:/backup \
  ubuntu tar czf /backup/llama_cache_$(date +%Y%m%d_%H%M%S).tar.gz -C /data .

# 3. Restore from known good backup
# Extract backup tarball to volume

# 4. Restart services
docker compose up -d
```

---

## 11. Agent-Specific Guidelines

### 11.1 Autonomous Agent Rules

**Claude Code and other AI agents MUST follow these rules:**

1. **NEVER modify credentials** (.env file) without explicit human approval
2. **NEVER use `git add -A`** or `git add .` - always add specific files
3. **NEVER push to remote** without explicit human approval
4. **NEVER run destructive commands** (`rm -rf`, `docker compose down -v`, `DROP TABLE`) without explicit approval
5. **ALWAYS backup** before configuration changes
6. **ALWAYS test** changes before committing
7. **ALWAYS use scratchpad** for temporary files (never /tmp directly)
8. **ALWAYS write descriptive commit messages** following section 3.3 format

### 11.2 Agent Capabilities Matrix

| Capability | Autonomous | Requires Approval | Forbidden |
|------------|------------|-------------------|-----------|
| Read files | ✅ | | |
| Read logs | ✅ | | |
| Run tests | ✅ | | |
| Modify code | | ✅ | |
| Modify configuration | | ✅ | |
| Commit changes | ✅ (after approval) | | |
| Push changes | | ✅ | |
| Rotate credentials | | ✅ | |
| Delete volumes | | | ❌ |
| Modify .env | | | ❌ (without approval) |
| Execute sudo commands | | | ❌ |

### 11.3 Agent Workflow Template

**When asked to implement a feature/fix:**

```plaintext
1. Acknowledge task
   "I'll implement [feature/fix] following these steps:"

2. Read relevant files
   - Use Read tool to examine current code
   - Check documentation (CLAUDE.md, AGENTS.md)
   - Review related issues (SecurityAudit.md)

3. Plan implementation
   - List specific files to modify
   - Identify affected services
   - Determine testing approach

4. Request approval if needed
   "This change affects [services]. May I proceed?"

5. Implement change
   - Make modifications
   - Follow coding standards
   - Add comments where needed

6. Test change
   - Run component tests
   - Run integration tests
   - Document test results

7. Commit change
   - Stage specific files
   - Write descriptive commit message
   - Show commit summary

8. Report completion
   "Change implemented and tested. Summary:
   - Modified: [files]
   - Tests passed: [test results]
   - Next steps: [if any]"
```

### 11.4 Agent Communication Standards

**When reporting status:**

✅ **GOOD:**
```
Modified ai-proxy/proxy.py to add rate limiting.
Tests passed: 100 req/min limit enforced correctly.
Ready to commit.
```

❌ **BAD:**
```
I made some changes to improve performance. Everything should be fine now.
```

**When asking for approval:**

✅ **GOOD:**
```
I need to modify .env to add a new RATE_LIMIT_PER_MINUTE variable.
This requires your approval as it changes credentials/configuration.
Proposed change:
  RATE_LIMIT_PER_MINUTE=100
May I proceed?
```

❌ **BAD:**
```
I'll just update the config file real quick.
```

### 11.5 Error Handling for Agents

**When encountering errors:**

1. **Capture error details**
   ```bash
   docker compose logs <service> > $SCRATCHPAD/error.log
   ```

2. **Analyze error**
   - Read error message
   - Check recent changes
   - Review related logs

3. **Report findings**
   ```
   ERROR: llama-server failed to start
   Error message: "CUDA out of memory"
   Probable cause: Model too large for available VRAM
   Suggested solution: Reduce context length in models.ini
   ```

4. **Propose solution**
   - List specific remediation steps
   - Indicate if approval needed
   - Provide rollback plan

**NEVER guess or randomly try solutions. Always analyze and propose specific fixes.**

---

## Appendices

### Appendix A: Quick Reference Commands

```bash
# Service Management
docker compose ps                         # List services
docker compose up -d --build <service>    # Rebuild service
docker compose restart <service>          # Restart service
docker compose logs -f <service>          # View logs

# Testing
./integration-test.sh                     # Run integration tests
./security-test.sh                        # Run security tests
curl -H "X-API-Key: $METRICS_API_KEY" http://localhost:3001/metrics | jq .

# Git Operations
git status                                # Check repo status
git log --oneline -10                     # View recent commits
git add <specific-file>                   # Stage file
git commit -m "type(scope): message"      # Commit changes

# Database
docker exec -it ai-supabase-db-1 psql -U postgres -d postgres
# SQL: SELECT * FROM api_keys;
# SQL: SELECT * FROM profiles;

# GPU Monitoring
nvidia-smi                                # GPU status
curl -s -H "X-API-Key: $METRICS_API_KEY" http://localhost:3001/metrics | jq .gpus

# Debugging
docker stats --no-stream                  # Resource usage
docker network inspect ai-stack-net       # Network details
docker compose exec <service> sh          # Shell into container
```

### Appendix B: Service Dependency Graph

```
┌─────────────────┐
│  temper-view    │
│  (Frontend)     │
└────────┬────────┘
         │
    ┌────┴────────────────────────┐
    │                             │
┌───▼────────┐             ┌──────▼──────┐
│ fan-manager│             │  Supabase   │
│  (temper)  │             │   (Auth)    │
└───┬────────┘             └──────┬──────┘
    │                             │
    │  ┌──────────────────────────┘
    │  │
┌───▼──▼─────────┐         ┌──────────────┐
│  llama-server  │◄────────│ ai-proxy  │
│  (Inference)   │         │   (Gateway)  │
└────────────────┘         └──────┬───────┘
         │                        │
         │                        │
         │               ┌────────▼────────┐
         └──────────────►│   PostgreSQL    │
                         │   (Database)    │
                         └─────────────────┘
```

### Appendix C: Emergency Contacts

**Human Administrators:**
- Primary: tyler (SSH: tyler@<server-ip>)
- Email: [Configure in SecurityAudit.md]

**External Services:**
- Stripe Support: https://support.stripe.com
- Supabase Support: https://supabase.com/support

**Documentation:**
- llama.cpp: https://github.com/ggerganov/llama.cpp
- NVML API: https://developer.nvidia.com/nvidia-management-library-nvml
- Supabase Docs: https://supabase.com/docs

---

## Document Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-01-29 | Claude Sonnet 4.5 | Initial comprehensive operator's manual created |

---

**END OF DOCUMENT**

For technical architecture details, see **CLAUDE.md**.
For security issues, see **SecurityAudit.md**.
