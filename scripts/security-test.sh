#!/bin/bash
# security-test.sh - AI Stack Security Test Suite
# Version: 1.0
# Usage: ./security-test.sh

set -e  # Exit on error

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

echo -e "${BLUE}=== AI Stack Security Test Suite ===${NC}"
echo "Started at: $(date)"
echo ""

TESTS_PASSED=0
TESTS_FAILED=0

# Helper function for test status
test_status() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}✅ PASS${NC}: $2"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        echo -e "${RED}❌ FAIL${NC}: $2"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
}

# Test 1: Unauthorized access blocked (metrics endpoint)
if [ -n "$METRICS_API_KEY" ]; then
    echo -e "${YELLOW}Test 1: Unauthorized access to metrics endpoint...${NC}"
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3001/metrics)
    if [ "$HTTP_CODE" = "401" ] || [ "$HTTP_CODE" = "403" ]; then
        test_status 0 "Metrics endpoint blocks unauthorized access"
    else
        test_status 1 "Metrics endpoint accessible without auth (got HTTP $HTTP_CODE)"
    fi
else
    echo -e "${YELLOW}Test 1: SKIPPED - METRICS_API_KEY not set${NC}"
fi

# Test 2: Valid authentication succeeds
if [ -n "$METRICS_API_KEY" ]; then
    echo -e "${YELLOW}Test 2: Valid authentication to metrics endpoint...${NC}"
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
        -H "X-API-Key: $METRICS_API_KEY" http://localhost:3001/metrics)
    if [ "$HTTP_CODE" = "200" ]; then
        test_status 0 "Valid authentication succeeds"
    else
        test_status 1 "Valid authentication rejected (got HTTP $HTTP_CODE)"
    fi
else
    echo -e "${YELLOW}Test 2: SKIPPED - METRICS_API_KEY not set${NC}"
fi

# Test 3: Invalid API key rejected
if [ -n "$METRICS_API_KEY" ]; then
    echo -e "${YELLOW}Test 3: Invalid API key rejected...${NC}"
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
        -H "X-API-Key: invalid_key_12345" http://localhost:3001/metrics)
    if [ "$HTTP_CODE" = "401" ] || [ "$HTTP_CODE" = "403" ]; then
        test_status 0 "Invalid API key rejected"
    else
        test_status 1 "Invalid API key accepted (got HTTP $HTTP_CODE)"
    fi
else
    echo -e "${YELLOW}Test 3: SKIPPED - METRICS_API_KEY not set${NC}"
fi

# Test 4: SQL injection attempt (safe test)
echo -e "${YELLOW}Test 4: SQL injection protection...${NC}"
# This should NOT crash the service or return SQL errors
RESPONSE=$(curl -s -X POST http://localhost:8081/v1/chat/completions \
    -H "Authorization: Bearer sk-ant-'; DROP TABLE api_keys; --" \
    -H "Content-Type: application/json" \
    -d '{"model":"claude-3-5-sonnet-20241022","messages":[{"role":"user","content":"test"}],"max_tokens":5}' 2>&1)

# Check service is still running
sleep 2
if [ -n "$LLAMA_API_KEY" ]; then
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
        -H "Authorization: Bearer $LLAMA_API_KEY" http://localhost:8082/chat/health)
    if [ "$HTTP_CODE" = "200" ]; then
        test_status 0 "SQL injection attempt did not crash service"
    else
        test_status 1 "Service crashed or unhealthy after SQL injection attempt"
    fi
else
    echo -e "${YELLOW}Test 4: SKIPPED - LLAMA_API_KEY not set${NC}"
fi

# Test 5: Credential leak check
echo -e "${YELLOW}Test 5: Credential leak in git repository...${NC}"
if git ls-files | grep -q "^\.env$"; then
    test_status 1 ".env file tracked in git"
else
    test_status 0 ".env not tracked in git"
fi

# Test 6: .env file permissions
echo -e "${YELLOW}Test 6: .env file permissions...${NC}"
if [ -f .env ]; then
    PERMS=$(stat -c %a .env)
    if [ "$PERMS" = "600" ] || [ "$PERMS" = "400" ]; then
        test_status 0 ".env has correct permissions ($PERMS)"
    else
        test_status 1 ".env has insecure permissions ($PERMS, should be 600)"
    fi
else
    echo -e "${YELLOW}Test 6: SKIPPED - .env file not found${NC}"
fi

# Test 7: Hardcoded credentials in code
echo -e "${YELLOW}Test 7: Hardcoded credentials in codebase...${NC}"
HARDCODED=$(grep -r "sk-ant-api" --include="*.py" --include="*.ts" --include="*.tsx" --include="*.cpp" \
    --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=build . 2>/dev/null | wc -l)
if [ "$HARDCODED" -eq 0 ]; then
    test_status 0 "No hardcoded credentials found in code"
else
    test_status 1 "Found $HARDCODED instances of hardcoded credentials"
fi

# Test 8: Hardcoded Supabase keys (from SecurityAudit.md issue #1)
echo -e "${YELLOW}Test 8: Hardcoded Supabase keys in frontend...${NC}"
if [ -d temper-view/src ]; then
    SUPABASE_KEYS=$(grep -r "eyJhbGciOi" temper-view/src/ 2>/dev/null | wc -l)
    if [ "$SUPABASE_KEYS" -eq 0 ]; then
        test_status 0 "No hardcoded Supabase keys in frontend"
    else
        test_status 1 "Found $SUPABASE_KEYS hardcoded Supabase keys (SecurityAudit.md #1)"
    fi
else
    echo -e "${YELLOW}Test 8: SKIPPED - temper-view/src not found${NC}"
fi

# Test 9: CORS header check
echo -e "${YELLOW}Test 9: CORS restrictions...${NC}"
CORS_HEADER=$(curl -s -I -H "Origin: https://evil.com" http://localhost:3001/metrics 2>&1 | grep -i "access-control-allow-origin" || true)
if [ -z "$CORS_HEADER" ]; then
    test_status 0 "No CORS headers (endpoint blocked by auth)"
elif echo "$CORS_HEADER" | grep -q "*"; then
    test_status 1 "CORS allows all origins (Access-Control-Allow-Origin: *) - SecurityAudit.md #4"
else
    test_status 0 "CORS properly restricted"
fi

# Test 10: CSP header check
echo -e "${YELLOW}Test 10: Content Security Policy headers...${NC}"
CSP_HEADER=$(curl -s -I http://localhost:3000 | grep -i "content-security-policy" || true)
if [ -n "$CSP_HEADER" ]; then
    test_status 0 "CSP headers present"
else
    test_status 1 "CSP headers missing - SecurityAudit.md #5"
fi

# Test 11: Service port exposure check
echo -e "${YELLOW}Test 11: Internal service port exposure...${NC}"
EXPOSED_PORTS=0
# llama-server should be internal only (localhost or not exposed)
if netstat -tuln 2>/dev/null | grep -q "0.0.0.0:8082"; then
    echo -e "${RED}  ⚠️  llama-server exposed on all interfaces${NC}"
    EXPOSED_PORTS=$((EXPOSED_PORTS + 1))
fi
# fan-manager should be localhost only
if netstat -tuln 2>/dev/null | grep "3001" | grep -q "0.0.0.0"; then
    echo -e "${RED}  ⚠️  fan-manager exposed on all interfaces${NC}"
    EXPOSED_PORTS=$((EXPOSED_PORTS + 1))
fi

if [ "$EXPOSED_PORTS" -eq 0 ]; then
    test_status 0 "Internal services properly isolated"
else
    test_status 1 "$EXPOSED_PORTS internal services exposed externally"
fi

# Test 12: Docker secrets in environment
echo -e "${YELLOW}Test 12: Secrets in Docker environment variables...${NC}"
SECRETS_IN_ENV=$(docker inspect llama-proxy 2>/dev/null | grep -i "password\|secret\|key" | grep -v "POSTGRES_PASSWORD" | grep -v "API_KEY" | wc -l || echo "0")
if [ "$SECRETS_IN_ENV" -gt 5 ]; then
    test_status 1 "Excessive secrets visible in Docker environment"
else
    test_status 0 "Docker environment variables acceptable"
fi

# Summary
echo ""
echo -e "${BLUE}=== Security Test Summary ===${NC}"
echo -e "Tests passed: ${GREEN}$TESTS_PASSED${NC}"
echo -e "Tests failed: ${RED}$TESTS_FAILED${NC}"
echo "Completed at: $(date)"
echo ""
echo -e "${YELLOW}Note: Review SecurityAudit.md for comprehensive security findings.${NC}"

if [ $TESTS_FAILED -gt 0 ]; then
    echo -e "${RED}Security tests FAILED${NC}"
    echo -e "${YELLOW}Action required: Address failed security tests before production deployment.${NC}"
    exit 1
else
    echo -e "${GREEN}All security tests PASSED${NC}"
    exit 0
fi
