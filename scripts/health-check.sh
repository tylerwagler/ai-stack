#!/bin/bash
# health-check.sh - Monitor AI Stack health
# Version: 1.0
# Usage: ./health-check.sh [--verbose] [--alert]
# Recommended: Add to crontab for periodic monitoring

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Load environment variables
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Parse arguments
VERBOSE=false
ALERT_MODE=false
LOG_FILE="/var/log/ai-stack-health.log"

while [[ $# -gt 0 ]]; do
    case $1 in
        --verbose|-v)
            VERBOSE=true
            shift
            ;;
        --alert|-a)
            ALERT_MODE=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--verbose] [--alert]"
            exit 1
            ;;
    esac
done

echo -e "${BLUE}=== AI Stack Health Check ===${NC}"
echo "Started at: $(date)"
echo ""

CRITICAL_ISSUES=0
WARNINGS=0

# Function to log alerts
log_alert() {
    local level=$1
    local message=$2
    echo "$(date '+%Y-%m-%d %H:%M:%S') [$level] $message" >> "$LOG_FILE" 2>/dev/null
    if [ "$ALERT_MODE" = true ]; then
        # Add notification method here (e.g., email, webhook)
        echo "ALERT: $message"
    fi
}

# Check 1: GPU Temperature
if [ -n "$METRICS_API_KEY" ]; then
    echo "Checking GPU temperature..."
    GPU_TEMP=$(curl -s -H "X-API-Key: $METRICS_API_KEY" http://localhost:3001/metrics 2>/dev/null | jq -r '.gpus[0].temperature' 2>/dev/null)

    if [ -n "$GPU_TEMP" ] && [ "$GPU_TEMP" != "null" ]; then
        if [ "$GPU_TEMP" -gt 85 ]; then
            echo -e "${RED}❌ CRITICAL: GPU temperature: ${GPU_TEMP}°C (>85°C threshold)${NC}"
            log_alert "CRITICAL" "GPU temperature critical: ${GPU_TEMP}°C"
            CRITICAL_ISSUES=$((CRITICAL_ISSUES + 1))
        elif [ "$GPU_TEMP" -gt 80 ]; then
            echo -e "${YELLOW}⚠️  WARNING: GPU temperature: ${GPU_TEMP}°C (>80°C threshold)${NC}"
            log_alert "WARNING" "GPU temperature high: ${GPU_TEMP}°C"
            WARNINGS=$((WARNINGS + 1))
        else
            echo -e "${GREEN}✅ OK: GPU temperature: ${GPU_TEMP}°C${NC}"
        fi

        if [ "$VERBOSE" = true ]; then
            curl -s -H "X-API-Key: $METRICS_API_KEY" http://localhost:3001/metrics 2>/dev/null | jq '.gpus[] | {id, temp: .temperature, util: .utilization, power: .power_watts}'
        fi
    else
        echo -e "${RED}❌ ERROR: Cannot read GPU temperature${NC}"
        CRITICAL_ISSUES=$((CRITICAL_ISSUES + 1))
    fi
else
    echo -e "${YELLOW}⚠️  SKIP: METRICS_API_KEY not set${NC}"
fi

# Check 2: Service Status
echo ""
echo "Checking service status..."
SERVICES_DOWN=0
for service in llama-server llama-proxy fan-manager temper-view ai-supabase-db-1; do
    if docker ps --format '{{.Names}}' | grep -q "^${service}$"; then
        echo -e "${GREEN}✅ OK: $service is running${NC}"
    else
        echo -e "${RED}❌ ERROR: $service is not running${NC}"
        log_alert "CRITICAL" "Service down: $service"
        SERVICES_DOWN=$((SERVICES_DOWN + 1))
        CRITICAL_ISSUES=$((CRITICAL_ISSUES + 1))
    fi
done

# Check 3: Disk Space
echo ""
echo "Checking disk space..."
DISK_USAGE=$(df -h / | awk 'NR==2 {print $5}' | sed 's/%//')
if [ "$DISK_USAGE" -gt 90 ]; then
    echo -e "${RED}❌ CRITICAL: Disk usage: ${DISK_USAGE}% (>90%)${NC}"
    log_alert "CRITICAL" "Disk usage critical: ${DISK_USAGE}%"
    CRITICAL_ISSUES=$((CRITICAL_ISSUES + 1))
elif [ "$DISK_USAGE" -gt 80 ]; then
    echo -e "${YELLOW}⚠️  WARNING: Disk usage: ${DISK_USAGE}% (>80%)${NC}"
    log_alert "WARNING" "Disk usage high: ${DISK_USAGE}%"
    WARNINGS=$((WARNINGS + 1))
else
    echo -e "${GREEN}✅ OK: Disk usage: ${DISK_USAGE}%${NC}"
fi

if [ "$VERBOSE" = true ]; then
    df -h | grep -E "Filesystem|/$|/home"
fi

# Check 4: RAM Usage
echo ""
echo "Checking RAM usage..."
RAM_USAGE=$(free | awk 'NR==2 {printf "%.0f", $3/$2*100}')
if [ "$RAM_USAGE" -gt 90 ]; then
    echo -e "${RED}❌ CRITICAL: RAM usage: ${RAM_USAGE}% (>90%)${NC}"
    log_alert "CRITICAL" "RAM usage critical: ${RAM_USAGE}%"
    CRITICAL_ISSUES=$((CRITICAL_ISSUES + 1))
elif [ "$RAM_USAGE" -gt 80 ]; then
    echo -e "${YELLOW}⚠️  WARNING: RAM usage: ${RAM_USAGE}% (>80%)${NC}"
    log_alert "WARNING" "RAM usage high: ${RAM_USAGE}%"
    WARNINGS=$((WARNINGS + 1))
else
    echo -e "${GREEN}✅ OK: RAM usage: ${RAM_USAGE}%${NC}"
fi

if [ "$VERBOSE" = true ]; then
    free -h
fi

# Check 5: Database Connectivity
echo ""
echo "Checking database connectivity..."
if docker exec ai-supabase-db-1 psql -U postgres -c "SELECT 1" > /dev/null 2>&1; then
    echo -e "${GREEN}✅ OK: Database is accessible${NC}"
else
    echo -e "${RED}❌ ERROR: Database is not accessible${NC}"
    log_alert "CRITICAL" "Database unreachable"
    CRITICAL_ISSUES=$((CRITICAL_ISSUES + 1))
fi

# Check 6: API Endpoints
echo ""
echo "Checking API endpoints..."

# llama-server health
if [ -n "$LLAMA_API_KEY" ]; then
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $LLAMA_API_KEY" http://localhost:8082/chat/health 2>/dev/null)
    if [ "$HTTP_CODE" = "200" ]; then
        echo -e "${GREEN}✅ OK: llama-server health check passed${NC}"
    else
        echo -e "${RED}❌ ERROR: llama-server health check failed (HTTP $HTTP_CODE)${NC}"
        log_alert "CRITICAL" "llama-server health check failed"
        CRITICAL_ISSUES=$((CRITICAL_ISSUES + 1))
    fi
fi

# Frontend
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3000 2>/dev/null)
if [ "$HTTP_CODE" = "200" ]; then
    echo -e "${GREEN}✅ OK: Frontend is accessible${NC}"
else
    echo -e "${RED}❌ ERROR: Frontend not accessible (HTTP $HTTP_CODE)${NC}"
    log_alert "CRITICAL" "Frontend not accessible"
    CRITICAL_ISSUES=$((CRITICAL_ISSUES + 1))
fi

# Check 7: Docker Resources
if [ "$VERBOSE" = true ]; then
    echo ""
    echo "Docker resource usage:"
    docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" | head -10
fi

# Check 8: Recent Errors in Logs
echo ""
echo "Checking for recent errors in logs..."
RECENT_ERRORS=$(docker compose logs --since 5m 2>&1 | grep -i "error\|fatal\|critical" | wc -l)
if [ "$RECENT_ERRORS" -gt 10 ]; then
    echo -e "${YELLOW}⚠️  WARNING: Found $RECENT_ERRORS errors in logs (last 5 minutes)${NC}"
    WARNINGS=$((WARNINGS + 1))
    if [ "$VERBOSE" = true ]; then
        docker compose logs --since 5m 2>&1 | grep -i "error\|fatal\|critical" | tail -10
    fi
else
    echo -e "${GREEN}✅ OK: No significant errors in recent logs${NC}"
fi

# Summary
echo ""
echo -e "${BLUE}=== Health Check Summary ===${NC}"
echo "Critical issues: $CRITICAL_ISSUES"
echo "Warnings: $WARNINGS"
echo "Completed at: $(date)"

if [ $CRITICAL_ISSUES -gt 0 ]; then
    echo -e "${RED}HEALTH CHECK FAILED - Critical issues detected${NC}"
    exit 2
elif [ $WARNINGS -gt 0 ]; then
    echo -e "${YELLOW}HEALTH CHECK WARNING - Minor issues detected${NC}"
    exit 1
else
    echo -e "${GREEN}HEALTH CHECK PASSED - All systems operational${NC}"
    exit 0
fi
