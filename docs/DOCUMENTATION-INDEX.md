# AI Stack Documentation Index

**Last Updated:** 2026-01-29

This document provides a comprehensive guide to all documentation, scripts, and operational procedures for the ai-stack platform.

---

## Documentation Structure

### Core Documentation

| Document | Purpose | Audience |
|----------|---------|----------|
| **[CLAUDE.md](./CLAUDE.md)** | Technical architecture, component details, basic commands | Developers, AI agents |
| **[AGENTS.md](./AGENTS.md)** | Comprehensive operator's manual with procedures and protocols | Autonomous agents, administrators |
| **[SecurityAudit.md](./SecurityAudit.md)** | Security findings and remediation checklist | Security team, administrators |
| **[supabase-credentials.md](./supabase-credentials.md)** | Supabase access credentials and endpoints | Administrators only |

### Component-Specific Documentation

| Location | Content |
|----------|---------|
| `temper/API.md` | GPU metrics API schema (100+ fields) |
| `temper/README.md` | C++ GPU control system documentation |
| `temper-view/README.md` | React frontend documentation |
| `llama.cpp/README.md` | LLM inference engine documentation |
| `llama-proxy/proxy.py` | Inline code documentation |

---

## Operational Scripts

### Testing & Validation

| Script | Purpose | Usage |
|--------|---------|-------|
| **[integration-test.sh](./integration-test.sh)** | Full stack integration testing | `./integration-test.sh` |
| **[security-test.sh](./security-test.sh)** | Security validation suite | `./security-test.sh` |
| **[credential-audit.sh](./credential-audit.sh)** | Check for credential leaks | `./credential-audit.sh` |

### Maintenance & Monitoring

| Script | Purpose | Usage |
|--------|---------|-------|
| **[health-check.sh](./health-check.sh)** | System health monitoring | `./health-check.sh [--verbose] [--alert]` |
| **[rotate-logs.sh](./rotate-logs.sh)** | Log rotation and cleanup | `./rotate-logs.sh` |
| **[update.sh](./update.sh)** | Update models, backup, restore | `./update.sh [--restore] [--check-only]` |

---

## Quick Reference by Task

### "I need to..."

#### Make code changes
1. Read [CLAUDE.md](./CLAUDE.md) for architecture understanding
2. Follow [AGENTS.md - Change Management](./AGENTS.md#1-change-management)
3. Run component tests before deployment
4. Follow [AGENTS.md - Git Workflow](./AGENTS.md#3-git-workflow) for commits

#### Test my changes
1. Run `./integration-test.sh` for full stack validation
2. Run `./security-test.sh` for security validation
3. Check [AGENTS.md - Testing Protocols](./AGENTS.md#2-testing-protocols)

#### Deploy to production
1. Review [SecurityAudit.md](./SecurityAudit.md) - ensure CRITICAL/HIGH issues resolved
2. Run `./credential-audit.sh` - check for credential leaks
3. Run `./security-test.sh` - validate security posture
4. Backup: `./update.sh --check-only`
5. Follow [AGENTS.md - Deployment Procedures](./AGENTS.md#8-deployment-procedures)

#### Troubleshoot an issue
1. Check [AGENTS.md - Troubleshooting Reference](./AGENTS.md#10-troubleshooting-reference)
2. Review service logs: `docker compose logs -f <service>`
3. Run `./health-check.sh --verbose`
4. Check [CLAUDE.md - Troubleshooting](./CLAUDE.md#troubleshooting)

#### Monitor system health
1. Run `./health-check.sh` periodically
2. Access temper-view dashboard: http://localhost:3000
3. Check GPU metrics: `curl -H "X-API-Key: $METRICS_API_KEY" http://localhost:3001/metrics | jq .`
4. Review [AGENTS.md - Monitoring & Alerting](./AGENTS.md#9-monitoring--alerting)

#### Manage credentials
1. Follow [AGENTS.md - Credential Management](./AGENTS.md#5-credential-management)
2. Run `./credential-audit.sh` to check for leaks
3. Review [supabase-credentials.md](./supabase-credentials.md) for Supabase access

#### Understand network topology
1. Review [AGENTS.md - Network Topology](./AGENTS.md#4-network-topology--access-control)
2. Check port accessibility matrix
3. Verify firewall rules for production

---

## Critical Reference Information

### Service Ports

| Service | Port | External Access | Auth Required |
|---------|------|-----------------|---------------|
| temper-view | 3000 | ✅ HTTP | Supabase JWT |
| llama-proxy | 8081 | ✅ HTTP | API Key (database) |
| llama-server | 8082 | ❌ Internal | LLAMA_API_KEY |
| fan-manager | 3001 | ❌ Localhost | METRICS_API_KEY |
| Supabase Kong | 8004 | ✅ HTTP | JWT tokens |
| Supabase Studio | 8003 | ⚠️ DEV ONLY | supabase/supabase |
| PostgreSQL | 5433 | ❌ Internal | Password |

### Environment Variables

Required variables in `.env`:
```bash
LLAMA_API_KEY=sk-ant-...          # Internal API authentication
METRICS_API_KEY=...               # Metrics endpoint auth
POSTGRES_PASSWORD=...             # Database password
POSTGRES_DB=postgres              # Database name
STRIPE_SECRET_KEY=sk_test_...     # Stripe API key
STRIPE_WEBHOOK_SECRET=whsec_...   # Webhook validation
IDRAC_IP=10.20.20.3              # Dell iDRAC IP (optional)
IDRAC_USER=temper                # iDRAC username
IDRAC_PASS=...                   # iDRAC password
```

### Key Commands

```bash
# Service management
docker compose up -d --build <service>    # Rebuild and restart service
docker compose logs -f <service>          # View logs
docker compose ps                         # List services
docker compose restart <service>          # Restart service

# Testing
./integration-test.sh                     # Full stack test
./security-test.sh                        # Security validation
./health-check.sh                         # Health monitoring

# Database access
docker exec -it ai-supabase-db-1 psql -U postgres -d postgres

# GPU monitoring
nvidia-smi                                # Host GPU status
curl -s -H "X-API-Key: $METRICS_API_KEY" http://localhost:3001/metrics | jq .

# Backup and restore
./update.sh --check-only                  # Verify backup capability
./update.sh --restore                     # Restore from backup
```

---

## Documentation Maintenance

### Adding New Documentation

1. Create document in appropriate location
2. Add entry to this index
3. Cross-reference in CLAUDE.md and/or AGENTS.md
4. Update revision history

### Updating Existing Documentation

1. Update the document
2. Update "Last Updated" date
3. Add entry to revision history (if applicable)
4. Update cross-references if needed

---

## Getting Help

### For Developers
- Start with [CLAUDE.md](./CLAUDE.md) for architecture overview
- Check component-specific README files
- Review code comments and inline documentation

### For Operators/Administrators
- Start with [AGENTS.md](./AGENTS.md) for operational procedures
- Use this index to find specific information
- Run `./health-check.sh` for system status

### For Security Auditors
- Review [SecurityAudit.md](./SecurityAudit.md)
- Run `./security-test.sh` for validation
- Run `./credential-audit.sh` for credential checks
- Check [AGENTS.md - Security Procedures](./AGENTS.md#7-security-procedures)

### For AI Agents
- Follow [AGENTS.md - Agent-Specific Guidelines](./AGENTS.md#11-agent-specific-guidelines)
- Read [CLAUDE.md](./CLAUDE.md) for system understanding
- Always consult [AGENTS.md](./AGENTS.md) before making changes

---

## Support & Resources

### Internal Resources
- Git Repository: `/home/tyler/ai-stack`
- Log Directory: `/home/tyler/ai-stack/llama-proxy/logs`
- Backup Directory: `/home/tyler/ai-stack/backups`
- Docker Volumes: `ai-stack_llama_cache`, `open-webui_data`

### External Resources
- llama.cpp: https://github.com/ggerganov/llama.cpp
- Supabase Docs: https://supabase.com/docs
- NVIDIA NVML: https://developer.nvidia.com/nvidia-management-library-nvml
- Stripe API: https://stripe.com/docs/api

---

**END OF DOCUMENTATION INDEX**
