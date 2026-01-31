#!/bin/bash
# credential-audit.sh - Check for credential leaks and security issues
# Version: 1.0
# Usage: ./credential-audit.sh

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "=== Credential Security Audit ==="
echo "Started at: $(date)"
echo ""

ISSUES_FOUND=0

# Check 1: .env in git
echo "Checking if .env is tracked in git..."
if git ls-files | grep -q "^\.env$"; then
    echo -e "${RED}❌ ERROR: .env is tracked in git!${NC}"
    echo "  Action: Run 'git rm --cached .env' to remove from tracking"
    ISSUES_FOUND=$((ISSUES_FOUND + 1))
else
    echo -e "${GREEN}✅ PASS: .env not tracked in git${NC}"
fi

# Check 2: Credentials in code
echo ""
echo "Checking for hardcoded credentials in code..."
CREDS=$(grep -r "sk-ant-api\|sk_test_\|sk_live_" \
    --include="*.py" --include="*.ts" --include="*.tsx" --include="*.cpp" --include="*.js" \
    --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=build \
    --exclude="*.md" --exclude="credential-audit.sh" . 2>/dev/null || true)

if [ -n "$CREDS" ]; then
    echo -e "${RED}❌ WARNING: Found hardcoded credentials in code:${NC}"
    echo "$CREDS" | head -10
    ISSUES_FOUND=$((ISSUES_FOUND + 1))
else
    echo -e "${GREEN}✅ PASS: No hardcoded credentials found${NC}"
fi

# Check 3: .env permissions
echo ""
echo "Checking .env file permissions..."
if [ -f .env ]; then
    PERMS=$(stat -c %a .env)
    if [ "$PERMS" = "600" ] || [ "$PERMS" = "400" ]; then
        echo -e "${GREEN}✅ PASS: .env has correct permissions ($PERMS)${NC}"
    else
        echo -e "${YELLOW}⚠️  WARNING: .env permissions are $PERMS (should be 600)${NC}"
        echo "  Action: Run 'chmod 600 .env'"
        chmod 600 .env
        echo "  Fixed: Changed permissions to 600"
    fi
else
    echo -e "${YELLOW}⚠️  WARNING: .env file not found${NC}"
fi

# Check 4: Supabase keys in frontend
echo ""
echo "Checking for hardcoded Supabase keys in frontend..."
if [ -d temper-view/src ]; then
    SUPABASE_KEYS=$(grep -r "eyJhbGciOi" temper-view/src/ 2>/dev/null | wc -l)
    if [ "$SUPABASE_KEYS" -gt 0 ]; then
        echo -e "${RED}❌ WARNING: Found $SUPABASE_KEYS hardcoded Supabase keys (SecurityAudit.md #1)${NC}"
        echo "  Action: Move keys to environment variables"
        ISSUES_FOUND=$((ISSUES_FOUND + 1))
    else
        echo -e "${GREEN}✅ PASS: No hardcoded Supabase keys found${NC}"
    fi
else
    echo -e "${YELLOW}⚠️  SKIP: temper-view/src not found${NC}"
fi

# Check 5: Passwords in docker-compose.yml
echo ""
echo "Checking for passwords in docker-compose.yml..."
if grep -q "PASSWORD=.*[^$]" docker-compose.yml 2>/dev/null; then
    echo -e "${RED}❌ WARNING: Found plaintext passwords in docker-compose.yml${NC}"
    echo "  Action: Use \${VARIABLE} syntax to reference .env"
    ISSUES_FOUND=$((ISSUES_FOUND + 1))
else
    echo -e "${GREEN}✅ PASS: No plaintext passwords in docker-compose.yml${NC}"
fi

# Check 6: Git history for credentials
echo ""
echo "Checking git history for credential leaks (last 100 commits)..."
LEAKED=$(git log -p -100 --all | grep -E "sk-ant-api|sk_test_|sk_live_|password.*=" | head -5 || true)
if [ -n "$LEAKED" ]; then
    echo -e "${YELLOW}⚠️  WARNING: Found potential credentials in git history${NC}"
    echo "  Note: This may require git history rewrite (dangerous operation)"
    echo "  First 5 matches:"
    echo "$LEAKED"
    ISSUES_FOUND=$((ISSUES_FOUND + 1))
else
    echo -e "${GREEN}✅ PASS: No credentials found in recent git history${NC}"
fi

# Check 7: Backup files with credentials
echo ""
echo "Checking for backup files containing credentials..."
BACKUPS=$(find . -name "*.env.bak" -o -name "*.env.backup" -o -name ".env.*" 2>/dev/null | grep -v ".env.example" || true)
if [ -n "$BACKUPS" ]; then
    echo -e "${YELLOW}⚠️  WARNING: Found .env backup files:${NC}"
    echo "$BACKUPS"
    echo "  Ensure these are properly secured (permissions 600) or deleted"
else
    echo -e "${GREEN}✅ PASS: No .env backup files found${NC}"
fi

# Check 8: Docker secrets exposure
echo ""
echo "Checking Docker secrets in environment variables..."
if docker compose ps > /dev/null 2>&1; then
    SECRET_COUNT=$(docker compose config 2>/dev/null | grep -i "password\|secret\|key" | wc -l)
    if [ "$SECRET_COUNT" -gt 20 ]; then
        echo -e "${YELLOW}⚠️  WARNING: Found $SECRET_COUNT secrets in docker-compose config${NC}"
        echo "  Note: Ensure all use \${VARIABLE} syntax"
    else
        echo -e "${GREEN}✅ PASS: Docker secrets properly referenced${NC}"
    fi
else
    echo -e "${YELLOW}⚠️  SKIP: Docker compose not available${NC}"
fi

# Summary
echo ""
echo "=== Audit Complete ==="
echo "Issues found: $ISSUES_FOUND"

if [ $ISSUES_FOUND -eq 0 ]; then
    echo -e "${GREEN}All credential security checks passed!${NC}"
    exit 0
else
    echo -e "${YELLOW}Action required: Address $ISSUES_FOUND security issues before deployment.${NC}"
    echo "See SecurityAudit.md for comprehensive security guidance."
    exit 1
fi
