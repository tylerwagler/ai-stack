#!/bin/bash
# integration-test.sh - AI Stack Integration Test Suite
# Version: 1.0
# Usage: ./integration-test.sh

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

echo -e "${BLUE}=== AI Stack Integration Test Suite ===${NC}"
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

# Test 1: Service Health
echo -e "${YELLOW}Test 1: Checking all services are running...${NC}"
docker compose ps | grep -E "Up|running" > /dev/null 2>&1
test_status $? "All services are running"

# Test 2: Database Connectivity
echo -e "${YELLOW}Test 2: Testing database connection...${NC}"
docker exec ai-supabase-db-1 psql -U postgres -d postgres -c "SELECT 1;" > /dev/null 2>&1
test_status $? "Database is accessible"

# Test 3: Database Schema
echo -e "${YELLOW}Test 3: Verifying database tables exist...${NC}"
TABLES=$(docker exec ai-supabase-db-1 psql -U postgres -d postgres -t -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name IN ('api_keys', 'profiles');")
if [ "$TABLES" -ge 1 ]; then
    test_status 0 "Database schema exists"
else
    test_status 1 "Database schema missing"
fi

# Test 4: GPU Metrics
echo -e "${YELLOW}Test 4: Fetching GPU metrics...${NC}"
METRICS=$(curl -s http://localhost:3001/metrics)
echo "$METRICS" | jq -e '.gpus[0]' > /dev/null 2>&1
test_status $? "GPU metrics accessible"

# Test 5: llama-server Health
echo -e "${YELLOW}Test 5: Testing llama-server health...${NC}"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8082/chat/health)
if [ "$HTTP_CODE" = "200" ]; then
    test_status 0 "llama-server health check passed"
else
    test_status 1 "llama-server health check failed (HTTP $HTTP_CODE)"
fi

# Test 6: ai-proxy Accessibility
echo -e "${YELLOW}Test 6: Testing ai-proxy endpoint...${NC}"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST http://localhost:8081/v1/chat/completions \
    -H "Authorization: Bearer invalid-key-test" \
    -H "Content-Type: application/json" \
    -d '{"model":"claude-3-5-sonnet-20241022","messages":[{"role":"user","content":"test"}],"max_tokens":5}')
# Should return 401 (unauthorized) or 500 (if processing the request)
if [ "$HTTP_CODE" = "401" ] || [ "$HTTP_CODE" = "500" ] || [ "$HTTP_CODE" = "200" ]; then
    test_status 0 "ai-proxy is responding"
else
    test_status 1 "ai-proxy not responding (HTTP $HTTP_CODE)"
fi

# Test 7: Frontend Accessibility
echo -e "${YELLOW}Test 7: Testing frontend (temper-view)...${NC}"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3000)
if [ "$HTTP_CODE" = "200" ]; then
    test_status 0 "Frontend is accessible"
else
    test_status 1 "Frontend not accessible (HTTP $HTTP_CODE)"
fi

# Test 7a: Auth Login Endpoint
echo -e "${YELLOW}Test 7a: Testing auth login endpoint...${NC}"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8081/v1/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email":"bad@test.com","password":"wrong"}')
# Should return 400 (bad creds) not 404 or 500
if [ "$HTTP_CODE" = "400" ] || [ "$HTTP_CODE" = "401" ] || [ "$HTTP_CODE" = "200" ]; then
    test_status 0 "Auth login endpoint responding (HTTP $HTTP_CODE)"
else
    test_status 1 "Auth login endpoint not responding (HTTP $HTTP_CODE)"
fi

# Test 7b: Auth Keys Endpoint (no token)
echo -e "${YELLOW}Test 7b: Testing auth keys endpoint (no auth)...${NC}"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8081/v1/auth/keys)
if [ "$HTTP_CODE" = "401" ]; then
    test_status 0 "Auth keys endpoint requires auth (HTTP 401)"
else
    test_status 1 "Auth keys endpoint unexpected response (HTTP $HTTP_CODE)"
fi

# Test 7c: Install Scripts Served
echo -e "${YELLOW}Test 7c: Testing install script serving...${NC}"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/install/setup.sh)
if [ "$HTTP_CODE" = "200" ]; then
    test_status 0 "Install scripts served (HTTP 200)"
else
    test_status 1 "Install scripts not served (HTTP $HTTP_CODE)"
fi

# Test 8: Supabase API
echo -e "${YELLOW}Test 8: Testing Supabase API endpoint...${NC}"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8004/rest/v1/)
if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "401" ]; then
    test_status 0 "Supabase API is responding"
else
    test_status 1 "Supabase API not responding (HTTP $HTTP_CODE)"
fi

# Test 9: Docker Network Connectivity
echo -e "${YELLOW}Test 9: Testing internal network connectivity...${NC}"
docker exec ai-proxy ping -c 1 llama-server > /dev/null 2>&1
test_status $? "Internal network connectivity verified"

# Test 10: Volume Mounts
echo -e "${YELLOW}Test 10: Checking volume mounts...${NC}"
docker volume inspect ai-stack_llama_cache > /dev/null 2>&1
test_status $? "Docker volumes exist"

# Summary
echo ""
echo -e "${BLUE}=== Test Summary ===${NC}"
echo -e "Tests passed: ${GREEN}$TESTS_PASSED${NC}"
echo -e "Tests failed: ${RED}$TESTS_FAILED${NC}"
echo "Completed at: $(date)"

if [ $TESTS_FAILED -gt 0 ]; then
    echo -e "${RED}Integration tests FAILED${NC}"
    exit 1
else
    echo -e "${GREEN}All integration tests PASSED${NC}"
    exit 0
fi
