# Claude-Local Login Feature

## Overview

The `claude-local` scripts now support **automatic API key retrieval** through user authentication. Users can log in to their AI Stack account and the script will automatically fetch their existing API keys or create a new one.

## Implementation Details

### Authentication Flow

1. **User Login**: Prompts for email/password
2. **Supabase Authentication**: Authenticates via JWT tokens
3. **API Key Retrieval**: Fetches user's existing API keys from PostgreSQL
4. **Key Selection**: User can choose an existing key or create new one
5. **Auto-Configuration**: Selected/created key is saved to config file

### Platforms Supported

| Platform | Script | Login Support | Notes |
|----------|--------|---------------|-------|
| Linux/macOS | `claude-local` | ✅ Yes | Full support via bash + curl |
| Windows PowerShell | `claude-local.ps1` | ✅ Yes | Full support via PowerShell |
| Windows Batch | `claude-local.bat` | ❌ No | Basic version, manual key only |

## Usage

### Linux/macOS

```bash
./claude-local --login
```

**Output:**
```
Login to AI Stack

Email: user@example.com
Password: ********
Authenticating with AI Stack...
Authentication successful! Retrieving API keys...

Found 2 existing API key(s):
  1. claude-local-20260125-143022 (created: 2026-01-25)
  2. my-production-key (created: 2026-01-20)

Select a key to use (1-2), or press Enter to create a new one: 1
Using existing API key.

✓ API key configured successfully!
  Config saved to: /home/user/.config/claude-local/env

You can now use: ./claude-local "Your prompt"
```

### Windows PowerShell

```powershell
.\claude-local.ps1 -Login
```

Same interactive flow as Linux.

## Technical Architecture

### Authentication Endpoints

**IMPORTANT**: Supabase is exposed through temper-view (port 3000), NOT ai-proxy (port 8081)

- **Auth URL**: `http://{AI_STACK_IP}:3000/auth/v1/token?grant_type=password`
- **REST API**: `http://{AI_STACK_IP}:3000/rest/v1/api_keys`
- **LLM Inference**: `http://{AI_STACK_IP}:8081/v1/chat/completions` (ai-proxy)
- **Method**: Supabase JWT authentication

The temper-view Nginx reverse proxy routes:
- `/auth/v1/*` → Supabase Kong (authentication)
- `/rest/v1/*` → Supabase Kong (database API)
- `/api/*` → temper metrics backend

### API Key Generation

**Format**: `sk_ai_` + 30 hex characters

**Security**:
- **Linux**: Uses `openssl rand -hex 16` (cryptographically secure)
- **Windows**: Uses `[Security.Cryptography.RNGCryptoServiceProvider]` (cryptographically secure)

**Example**: `sk_ai_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5`

### Configuration Storage

**Linux/macOS**:
- Path: `~/.config/claude-local/env`
- Format: Bash export statements
- Permissions: 600 (read/write owner only)

**Windows**:
- Path: `%USERPROFILE%\.config\claude-local\config.ps1`
- Format: PowerShell variable assignments
- Permissions: User-only access via NTFS

### Database Integration

**Tables Used**:
- `auth.users` - Supabase user authentication
- `public.api_keys` - User API keys with usage tracking
- `public.profiles` - User tier and subscription info

**Row-Level Security**:
- Users can only see/create their own keys
- Admins can view all keys

## Security Considerations

### Implemented Security

✅ **Cryptographically secure key generation** (openssl/RNG)
✅ **Secure password input** (hidden from terminal)
✅ **JWT-based authentication** (standard Supabase flow)
✅ **Config file permissions** (600 on Linux)
✅ **API key masking** in output (shows *********)

### Known Limitations

⚠️ **API keys stored in plaintext** in config files
- Config files are user-only readable (600 perms)
- Same as how most CLI tools store API keys
- Users should keep config directory secure

⚠️ **Hardcoded Supabase anon key**
- Required for public authentication endpoint
- Standard Supabase practice for public clients
- Backend enforces RLS policies

⚠️ **Password sent over HTTP** if BASE_URL is http://
- Should use HTTPS in production
- Local network usage assumes trusted network

## Testing

### Automated Test

Run the test script to verify infrastructure:

```bash
./test-login-flow.sh
```

**Tests performed**:
1. Supabase health endpoint accessibility
2. Authentication endpoint response
3. API key generation format
4. REST API endpoint availability
5. claude-local commands functionality

### Manual Testing

**Prerequisites**:
1. AI Stack running: `docker compose up -d`
2. Registered user account in Supabase
3. Network connectivity to AI Stack

**Test steps**:
```bash
# 1. Verify config is empty
./claude-local --config

# 2. Login with credentials
./claude-local --login
# Enter email and password when prompted

# 3. Verify key was saved
./claude-local --config

# 4. Test inference
./claude-local "Say hello"

# 5. Verify usage tracking in database
docker exec -it ai-supabase-db-1 psql -U postgres -c "SELECT * FROM usage_logs ORDER BY created_at DESC LIMIT 5;"
```

## User Workflows

### New User Setup

```bash
# 1. Login (will auto-create first key)
./claude-local --login
# Email: newuser@example.com
# Password: ********
# Press Enter to create new key

# 2. Start using
./claude-local "Hello world"
```

### Existing User with Multiple Keys

```bash
# 1. Login to see all keys
./claude-local --login
# Select key from list or create new

# 2. Switch to different key later
./claude-local --login
# Select different key number
```

### Manual Key Setup (Fallback)

```bash
# If login doesn't work, use manual setup
./claude-local --set-key sk_ai_your_key_here
```

## Error Handling

### Authentication Errors

**Error**: "Authentication failed - Invalid login credentials"
**Solution**: Check email/password, verify account exists

**Error**: "Could not connect to AI Stack"
**Solution**: Verify AI Stack is running, check BASE_URL

### API Key Errors

**Error**: "Failed to create API key"
**Solution**: Check database permissions, verify user has `profiles` entry

**Error**: "Failed to fetch API keys"
**Solution**: Check Supabase RLS policies, verify network connectivity

### Configuration Errors

**Error**: "API key not configured"
**Solution**: Run `./claude-local --login` or `--set-key`

## Advantages Over Manual Setup

| Feature | Manual Setup | Login Feature |
|---------|--------------|---------------|
| Key Discovery | Must find in web UI | Automatic list |
| Key Creation | Must open browser | One command |
| Multi-key Management | Manual copy/paste | Interactive selection |
| Usage Tracking | No connection | Linked to account |
| User Experience | 5+ steps | 1 command |
| Error Prone | Yes (copy/paste) | No (automated) |

## Future Enhancements

### Potential Improvements

1. **Key Rotation**: Automatic key expiration and renewal
2. **OAuth Flow**: Browser-based OAuth for better security
3. **Biometric Auth**: Touch ID/Windows Hello integration
4. **Multi-Account**: Support for multiple AI Stack accounts
5. **Key Metadata**: Display usage stats during selection
6. **HTTPS Enforcement**: Require HTTPS for login in production
7. **Session Caching**: Cache auth token for faster re-auth

### Integration Opportunities

- **CI/CD**: Generate temporary keys for automated workflows
- **Team Management**: Share keys across team members
- **Key Scopes**: Limit keys to specific models or rate limits
- **Audit Logging**: Track key creation via login

## Troubleshooting Guide

### Python Not Found

**Error**: `python3: command not found`
**Solution**: Install Python 3 or use `--set-key` for manual setup

### curl Issues

**Error**: Connection timeout
**Solution**:
- Check AI Stack is running: `docker compose ps`
- Verify network: `ping 10.20.10.5`
- Check firewall settings

### Database Connection

**Error**: API key creation fails
**Solution**:
```bash
# Check Supabase is healthy
docker exec -it ai-supabase-db-1 pg_isready

# Verify profiles table exists
docker exec -it ai-supabase-db-1 psql -U postgres -c "\dt public.profiles"

# Check RLS policies
docker exec -it ai-supabase-db-1 psql -U postgres -c "\d public.api_keys"
```

## Files Modified/Created

### New Files
- `claude-local.ps1` - PowerShell version with login support
- `test-login-flow.sh` - Automated testing script
- `LOGIN-FEATURE.md` - This documentation

### Modified Files
- `claude-local` - Added `--login` command and auth functions
- `claude-local.bat` - Added note about PowerShell version
- `claude-local-README.md` - Added login documentation

## Changelog

### Version 2.0 (2026-01-29)

**Added**:
- ✅ `--login` command for automatic API key retrieval
- ✅ Interactive key selection for users with multiple keys
- ✅ Secure key generation using cryptographic RNG
- ✅ PowerShell version with full parity
- ✅ Comprehensive error handling
- ✅ Automated testing script

**Improved**:
- ✅ Configuration management with persistent storage
- ✅ User experience with clear prompts and feedback
- ✅ Security with proper file permissions

**Maintained**:
- ✅ Backward compatibility with `--set-key` manual setup
- ✅ Auto-detection of BASE_URL
- ✅ Interactive model selection
