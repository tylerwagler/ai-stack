# AI Stack Quick Reference Card

**Version:** 1.0 | **Date:** 2026-01-29

---

## Emergency Commands

```bash
# Stop all services
docker compose down

# Restore from backup
./update.sh --restore

# Restart everything
docker compose up -d --force-recreate

# Check system health
./health-check.sh --verbose
```

---

## Daily Operations

### Check Status
```bash
docker compose ps                         # List all services
./health-check.sh                         # Health check
docker compose logs -f                    # Live logs (all services)
nvidia-smi                                # GPU status
```

### View Logs
```bash
docker compose logs -f llama-proxy        # Proxy logs
docker compose logs -f fan-manager        # Fan control logs
docker compose logs -f llama-server       # Inference logs
docker compose logs --tail=100 <service>  # Last 100 lines
```

### Restart Services
```bash
docker compose restart <service>          # Restart specific service
docker compose up -d --build <service>    # Rebuild and restart
docker compose restart                    # Restart all
```

---

## Testing & Validation

```bash
./integration-test.sh                     # Full stack integration test
./security-test.sh                        # Security validation
./credential-audit.sh                     # Check for credential leaks
./health-check.sh --verbose               # Detailed health check
```

---

## GPU Monitoring

```bash
# Host GPU status
nvidia-smi
nvidia-smi -l 1                           # Continuous monitoring

# Via API (requires METRICS_API_KEY)
curl -s -H "X-API-Key: $METRICS_API_KEY" \
  http://localhost:3001/metrics | jq .

# GPU temperature only
curl -s -H "X-API-Key: $METRICS_API_KEY" \
  http://localhost:3001/metrics | jq '.gpus[0].temperature'
```

---

## Database Operations

```bash
# Connect to PostgreSQL
docker exec -it ai-supabase-db-1 psql -U postgres -d postgres

# Useful queries
SELECT id, key_prefix, created_at FROM api_keys;
SELECT id, email, subscription_status FROM profiles;
SELECT table_name FROM information_schema.tables WHERE table_schema='public';

# Database backup (via pg_dump)
docker exec ai-supabase-db-1 pg_dump -U postgres postgres > backup.sql
```

---

## API Testing

### Test GPU Metrics
```bash
curl -H "X-API-Key: $METRICS_API_KEY" \
  http://localhost:3001/metrics | jq .
```

### Test LLM Inference
```bash
curl -X POST http://localhost:8081/v1/chat/completions \
  -H "Authorization: Bearer sk-ant-YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-3-5-sonnet-20241022",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 100
  }'
```

### Test llama-server Health
```bash
curl -H "Authorization: Bearer $LLAMA_API_KEY" \
  http://localhost:8082/chat/health
```

---

## Git Operations

```bash
# Check status
git status
git log --oneline -10

# Stage specific files
git add docker-compose.yml
git add llama-proxy/proxy.py

# Commit with proper format
git commit -m "$(cat <<'EOF'
fix(proxy): description

Detailed explanation of changes.

Tested with: [test description]

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
EOF
)"

# View commit
git log -1 --stat
```

---

## Troubleshooting

### Service Won't Start
```bash
docker compose logs <service>             # Check logs
docker compose ps                         # Check status
docker volume ls                          # Check volumes
docker network ls                         # Check networks
```

### GPU Not Detected
```bash
nvidia-smi                                # Test host GPU
docker run --rm --gpus all nvidia/cuda:13.1-base nvidia-smi
docker compose up -d --build fan-manager
docker exec fan-manager nvidia-smi
```

### Database Connection Failed
```bash
docker compose ps db                      # Check if running
docker compose logs db                    # Check logs
docker exec ai-supabase-db-1 psql -U postgres -c "SELECT 1"
```

### High GPU Temperature
```bash
# Check current temp
curl -s -H "X-API-Key: $METRICS_API_KEY" \
  http://localhost:3001/metrics | jq '.gpus[0].temperature'

# Check fan curves (in docker-compose.yml)
# FAN_SETPOINTS=50:30 70:65 78:95 80:100
# Format: temp:fan_speed_percent

# Restart fan manager
docker compose restart fan-manager
```

### Out of Disk Space
```bash
df -h                                     # Check disk usage
docker system df                          # Docker disk usage
docker system prune -af                   # Clean Docker (WARNING)
./rotate-logs.sh                          # Rotate logs
```

---

## Configuration

### Environment Variables (.env)
```bash
# View (without exposing secrets)
cat .env | grep -v "KEY\|PASSWORD\|SECRET"

# Test if loaded
docker compose config | grep -i llama_api_key

# Change fan setpoints (edit docker-compose.yml)
# FAN_SETPOINTS=50:30 70:65 78:95 80:100
# CHASSIS_FAN_SETPOINTS=45:20 55:30 65:70 75:100
# POWER_SETPOINTS=70:230 80:175 85:125
```

### Model Switching
```bash
# Edit docker-compose.yml
# Change: DEFAULT_MODEL=GLM 4.7 Flash
# To:     DEFAULT_MODEL=Nemotron-3-Nano-30B-A3B

docker compose up -d llama-proxy
docker compose restart llama-server
```

---

## Web Access

| Service | URL | Credentials |
|---------|-----|-------------|
| **Dashboard** | http://localhost:3000 | Supabase Auth |
| **Supabase Studio** | http://localhost:8003 | supabase/supabase |
| **Metrics API** | http://localhost:3001/metrics | METRICS_API_KEY header |

---

## Critical Paths

| Resource | Location |
|----------|----------|
| **Project Root** | `/home/tyler/ai-stack` |
| **Environment** | `/home/tyler/ai-stack/.env` |
| **Logs** | `/home/tyler/ai-stack/llama-proxy/logs` |
| **Backups** | `/home/tyler/ai-stack/backups` |
| **Models** | Docker volume: `ai-stack_llama_cache` |
| **Documentation** | `/home/tyler/ai-stack/CLAUDE.md` |
| | `/home/tyler/ai-stack/AGENTS.md` |

---

## Service Dependencies

```
temper-view (3000) → fan-manager (3001) → GPU Hardware
                   → Supabase (8004) → PostgreSQL (5433)

llama-proxy (8081) → llama-server (8082) → GPU Hardware
                   → PostgreSQL (5433)
```

---

## Alert Thresholds

| Metric | Warning | Critical |
|--------|---------|----------|
| GPU Temp | >80°C | >85°C |
| Disk Usage | >80% | >90% |
| RAM Usage | >80% | >90% |
| GPU Util | >95% | 100% sustained |

---

## Security Checklist

```bash
# Before deployment
./credential-audit.sh                     # Check for leaks
./security-test.sh                        # Validate security
cat SecurityAudit.md                      # Review issues

# Verify firewall (production)
sudo ufw status
netstat -tuln | grep LISTEN
```

---

## Crontab Recommendations

```cron
# Health monitoring (every 5 minutes)
*/5 * * * * /home/tyler/ai-stack/health-check.sh --alert >> /var/log/health.log 2>&1

# Log rotation (daily at 2 AM)
0 2 * * * /home/tyler/ai-stack/rotate-logs.sh >> /var/log/logrotate.log 2>&1

# Security audit (weekly Sunday 3 AM)
0 3 * * 0 /home/tyler/ai-stack/credential-audit.sh >> /var/log/security.log 2>&1

# Integration test (daily at 4 AM)
0 4 * * * /home/tyler/ai-stack/integration-test.sh >> /var/log/integration.log 2>&1
```

---

## Support Contacts

- **Documentation**: See DOCUMENTATION-INDEX.md
- **Architecture**: See CLAUDE.md
- **Operations**: See AGENTS.md
- **Security**: See SecurityAudit.md

---

**For comprehensive information, consult:**
- **[CLAUDE.md](./CLAUDE.md)** - Technical architecture
- **[AGENTS.md](./AGENTS.md)** - Operational procedures
- **[DOCUMENTATION-INDEX.md](./DOCUMENTATION-INDEX.md)** - Full documentation index
