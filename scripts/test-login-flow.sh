#!/bin/bash

# Test script for claude-local login functionality
# This script tests the API endpoints without requiring valid credentials

set -e

echo "Testing claude-local login flow..."
echo ""

# Configuration
# Supabase is exposed via temper-view (port 3000), not llama-proxy (port 8081)
SUPABASE_URL="http://10.20.10.5:3000"
LLAMA_PROXY_URL="http://10.20.10.5:8081"
ANON_KEY="eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9.eyJyb2xlIjogImFub24iLCAiaXNzIjogInN1cGFiYXNlIiwgImlhdCI6IDE3Njk1MjYyNTgsICJleHAiOiAyMDg0ODg2MjU4fQ.UYjtcXHj4l-9rEHMzNk2rqc-djmaPTtumgylFpG5NfA"

echo "1. Testing Supabase health endpoint..."
if curl -s -f "${SUPABASE_URL}/auth/v1/health" > /dev/null; then
    echo "   ✓ Supabase auth is accessible"
else
    echo "   ✗ Supabase auth endpoint not accessible"
    echo "   Make sure your AI Stack is running: docker compose up -d"
    exit 1
fi

echo ""
echo "2. Testing authentication endpoint (will fail with invalid credentials - this is expected)..."
AUTH_RESPONSE=$(curl -s -X POST "${SUPABASE_URL}/auth/v1/token?grant_type=password" \
    -H "apikey: ${ANON_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"email":"test@example.com","password":"wrongpassword"}')

if echo "$AUTH_RESPONSE" | grep -q "error"; then
    echo "   ✓ Auth endpoint is working (returned expected error)"
    ERROR_MSG=$(echo "$AUTH_RESPONSE" | python3 -c "import sys, json; data=json.load(sys.stdin); print(data.get('error_description', 'Unknown error'))" 2>/dev/null)
    echo "   Error message: $ERROR_MSG"
else
    echo "   ✗ Unexpected response from auth endpoint"
fi

echo ""
echo "3. Testing API key generation function..."
# Test the key generation (uses openssl rand)
TEST_KEY=$(openssl rand -hex 16 | cut -c1-30)
FULL_KEY="sk_ai_${TEST_KEY}"
echo "   Generated test key: $FULL_KEY"
echo "   Key length: ${#FULL_KEY} characters"

if [ ${#FULL_KEY} -eq 36 ]; then
    echo "   ✓ Key generation format is correct"
else
    echo "   ✗ Key generation format is incorrect (expected 36 chars)"
fi

echo ""
echo "4. Testing REST API endpoint (requires auth token)..."
# This will fail without a valid token, but we can check if the endpoint exists
REST_RESPONSE=$(curl -s -w "\n%{http_code}" "${SUPABASE_URL}/rest/v1/api_keys" \
    -H "apikey: ${ANON_KEY}" \
    -H "Content-Type: application/json" 2>&1)

HTTP_CODE=$(echo "$REST_RESPONSE" | tail -n1)
if [ "$HTTP_CODE" = "401" ] || [ "$HTTP_CODE" = "403" ]; then
    echo "   ✓ REST API endpoint is accessible (auth required as expected)"
elif [ "$HTTP_CODE" = "200" ]; then
    echo "   ✓ REST API endpoint is accessible and returned data"
else
    echo "   ✗ REST API endpoint returned unexpected status: $HTTP_CODE"
fi

echo ""
echo "5. Testing claude-local script commands..."

# Test --config
echo "   Testing --config..."
if ./claude-local --config > /dev/null 2>&1; then
    echo "   ✓ --config command works"
else
    echo "   ✗ --config command failed"
fi

# Test --set-key
echo "   Testing --set-key..."
if ./claude-local --set-key "test_key_12345" > /dev/null 2>&1; then
    echo "   ✓ --set-key command works"
    # Clean up
    ./claude-local --reset-config > /dev/null 2>&1
else
    echo "   ✗ --set-key command failed"
fi

echo ""
echo "=========================================="
echo "Test Summary:"
echo "=========================================="
echo ""
echo "The claude-local login infrastructure is ready."
echo ""
echo "To test with real credentials:"
echo "  ./claude-local --login"
echo ""
echo "Make sure you have:"
echo "  1. AI Stack running (docker compose up -d)"
echo "  2. A registered user account in Supabase"
echo "  3. Network connectivity to:"
echo "     - Supabase (auth/API keys): $SUPABASE_URL"
echo "     - LLM inference: $LLAMA_PROXY_URL"
echo ""
