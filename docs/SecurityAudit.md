# Security Audit Report: temper & temper-view Submodules

**Date:** 2026-01-28
**Audited Components:** temper (C++), temper-view (React)

---

## Executive Summary

This audit identified **1 CRITICAL**, **3 HIGH**, **6 MEDIUM**, and **2 LOW** severity security issues across the temper and temper-view submodules. The most critical findings involve hardcoded Supabase credentials and missing server-side authentication validation.

---

## Temper Submodule (C++ GPU Telemetry)

### Critical Issues

#### 1. Hardcoded Supabase Anon Key (Referenced in temper-view only)
**Severity:** CRITICAL
**Status:** Requires immediate fix
**Location:** `temper-view/src/lib/supabase.ts:5`

**Issue:** The Supabase anonymous key is hardcoded in the source code.

```typescript
const supabaseAnonKey = 'eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9.eyJyb2xlIjogImFub24iLCAiaXNzIjogInN1cGFiYXNlIiwgImlhdCI6IDE3Njk1MjYyNTgsICJleHAiOiAyMDg0ODg2MjU4fQ.UYjtcXHj4l-9rEHMzNk2rqc-djmaPTtumgylFpG5NfA';
```

**Risk:** Anyone with access to the frontend code can use this key to access the Supabase database directly, bypassing authentication.

**Recommendation:** Remove hardcoded key and use environment variables:
```typescript
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;
```

---

#### 2. API Key Only Checked Client-Side
**Severity:** CRITICAL
**Status:** Requires immediate fix
**Location:** `temper/src/MetricServer.cpp:276-297`

**Issue:** API key is checked from environment variable (`METRICS_API_KEY`) but validated only in HTTP request headers. There's no server-side verification against a trusted source.

**Code:**
```cpp
const char* envKey = std::getenv("METRICS_API_KEY");
std::string expectedKey = envKey ? envKey : "";

bool authorized = true;
if (!expectedKey.empty()) {
    std::string reqStr = request;
    std::string lowerReq = reqStr;
    std::transform(lowerReq.begin(), lowerReq.end(), lowerReq.begin(), ::tolower);

    // Check for X-API-Key header (original format)
    std::string lowerKeyLabel = "x-api-key: " + expectedKey;
    std::transform(lowerKeyLabel.begin(), lowerKeyLabel.end(), lowerKeyLabel.begin(), ::tolower);

    // Also check for Authorization: Bearer header (standard format)
    std::string lowerAuthLabel = "authorization: bearer " + expectedKey;
    std::transform(lowerAuthLabel.begin(), lowerAuthLabel.end(), lowerAuthLabel.begin(), ::tolower);

    if (lowerReq.find(lowerKeyLabel) == std::string::npos &&
        lowerReq.find(lowerAuthLabel) == std::string::npos) {
        authorized = false;
    }
}
```

**Risk:** An attacker can bypass authentication by crafting valid HTTP headers with the expected API key.

**Recommendation:** Implement server-side API key validation against a database or secure key management system.

---

### High Priority Issues

#### 3. Command Injection via IPMI Commands
**Severity:** HIGH
**Status:** Requires fix
**Location:** `temper/src/IpmiController.cpp` (multiple locations)

**Issue:** The code builds and executes external commands (`ipmitool`, `ipmi-sensors`) with user-provided or environment-provided parameters without proper sanitization.

**Code:**
```cpp
std::vector<std::string> buildIpmitoolArgs(const std::vector<std::string>& subcommand) {
    std::vector<std::string> args = {"ipmitool", "-I", "lanplus", "-H", host_, "-U", user_, "-P", pass_};

    // Retry settings: 1 auth retry, 3 session retries
    args.push_back("-N");
    args.push_back("1");
    args.push_back("-R");
    args.push_back("3");

    args.insert(args.end(), subcommand.begin(), subcommand.end());
    return args;
}
```

**Risk:** If `host_`, `user_`, `pass_` or subcommand arguments contain malicious input (e.g., `host_; rm -rf /`), command injection could occur.

**Recommendation:**
1. Validate all inputs against strict patterns
2. Use argument arrays properly
3. Consider switching to a library-based IPMI client
4. Implement input sanitization for all string parameters

---

#### 4. No CORS Restrictions
**Severity:** HIGH
**Status:** Requires fix
**Location:** Nginx configuration / `temper-view/src/api/gpuApi.ts`

**Issue:** The Nginx configuration shows `Access-Control-Allow-Origin: *` which allows any origin to access the API. Combined with the hardcoded Supabase key, this creates a significant security risk.

**Code (Nginx):**
```
Access-Control-Allow-Origin: *
```

**Risk:** Any website can make requests to the API endpoint with the anonymous key, potentially allowing data exfiltration or unauthorized access.

**Recommendation:** Restrict CORS to specific origins using the Nginx `add_header` directive:
```nginx
add_header Access-Control-Allow-Origin "https://yourdomain.com" always;
```

---

#### 5. No Content Security Policy (CSP)
**Severity:** HIGH
**Status:** Requires fix
**Location:** Nginx configuration

**Issue:** Missing Content Security Policy headers leave the application vulnerable to XSS attacks.

**Recommendation:** Implement CSP headers in Nginx:
```nginx
add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' data:; connect-src 'self' http://localhost:*;" always;
```

---

### Medium Priority Issues

#### 6. No Input Validation on Command Line Arguments
**Severity:** MEDIUM
**Status:** Should fix
**Location:** `temper/src/main.cpp:54-61`

**Issue:** Command-line arguments are concatenated without validation before being passed to `parseSetpoints()`.

**Code:**
```cpp
std::string command = argv[1];
if (command == "fanctl") {
    std::string fanArgs;
    for (int i = 2; i < argc; ++i) {
        fanArgs += argv[i];
        fanArgs += " ";
    }
    fanCurve.parseSetpoints(fanArgs);
```

**Risk:** Malicious arguments could cause crashes or unexpected behavior.

**Recommendation:** Validate command names and arguments before processing.

---

#### 7. JSON Injection Risk
**Severity:** MEDIUM
**Status:** Should fix
**Location:** `temper/src/MetricServer.cpp:40-223`

**Issue:** GPU names, serial numbers, and other fields are directly inserted into JSON without sanitization. If NVML returns malicious strings, they could be reflected back to clients.

**Code:**
```cpp
oss << "\"name\":\"" << m.name << "\","
    << "\"serial\":\"" << m.serial << "\","
```

**Risk:** Potential information disclosure or XSS if the frontend doesn't properly escape the response.

**Recommendation:** Validate and sanitize all string inputs before JSON serialization.

---

#### 8. No Rate Limiting on Metric Server
**Severity:** MEDIUM
**Status:** Should fix
**Location:** `temper/src/MetricServer.cpp:225-331`

**Issue:** The HTTP server accepts unlimited connections without rate limiting or connection pooling.

**Risk:** Denial of Service (DoS) vulnerability - an attacker could flood the server with connections.

**Recommendation:** Implement connection limits, timeout policies, and rate limiting.

---

#### 9. Curl Command Execution
**Severity:** MEDIUM
**Status:** Monitor
**Location:** `temper/src/LlamaMonitor.cpp:72-87`

**Issue:** Uses `curl` to make HTTP requests with timeout. While curl is relatively safe, the approach has potential issues.

**Code:**
```cpp
std::pair<int, std::string> LlamaMonitor::executeCurl(const std::string& url, int timeoutSec) {
    std::vector<std::string> args = {
        "curl", "-s", "-f", "--max-time", std::to_string(timeoutSec)
    };

    if (!apiKey_.empty()) {
        args.push_back("-H");
        args.push_back("Authorization: Bearer " + apiKey_);
    }

    args.push_back(url);
```

**Risk:** If `apiKey_` contains malicious characters, they could be reflected in the Authorization header.

**Recommendation:** Validate and sanitize the API key before use.

---

#### 10. Process Information Disclosure
**Severity:** LOW
**Status:** Informational
**Location:** `temper/src/NVMLManager.cpp:113-161`

**Issue:** Returns process names and PIDs to clients.

**Risk:** Limited - mainly information disclosure for reconnaissance.

**Recommendation:** Consider filtering or redacting sensitive process names if needed.

---

#### 11. Error Messages Expose System Information
**Severity:** LOW
**Status:** Informational
**Location:** `temper/src/main.cpp:271`

**Issue:** Error messages logged to stderr during the main loop could expose internal state.

**Recommendation:** Use generic error messages that don't reveal system details.

---

### Medium Priority Issues (Frontend)

#### 12. XSS via alert() Calls
**Severity:** MEDIUM
**Status:** Should fix
**Location:** `temper-view/src/components/portal/Portal.tsx` (multiple locations)

**Issue:** Uses `alert()` for error messages, which is a common XSS vector if error messages contain user-controlled input.

**Locations:**
- Line 78: `alert('Check your email for the confirmation link!');`
- Line 341: `alert('Failed to create API key');`
- Line 359: `alert('Failed to delete API key');`
- Line 476: `alert(`Billing Error: ${err.message}`);`
- Line 497: `alert(`Billing Error: ${err.message}`);`

**Risk:** If error messages contain malicious scripts, they will be executed.

**Recommendation:** Replace `alert()` with proper UI error components that sanitize content.

---

#### 13. localStorage for API Key Generation
**Severity:** MEDIUM
**Status:** Should review
**Location:** `temper-view/src/components/portal/Portal.tsx:324`

**Issue:** API key generation happens entirely on the client side with `Math.random()`.

**Code:**
```typescript
const key = `sk_ai_${Math.random().toString(36).substring(2, 15)}${Math.random().toString(36).substring(2, 15)}`;
```

**Risk:** Cryptographically insecure random number generation.

**Recommendation:** Use a cryptographically secure random number generator and generate keys server-side.

---

#### 14. No Input Validation on Remote Hosts
**Severity:** MEDIUM
**Status:** Should fix
**Location:** `temper-view/src/components/SettingsPage.tsx:64`

**Issue:** Host URLs are fetched without proper validation, potentially allowing SSRF (Server-Side Request Forgery).

**Code:**
```typescript
const response = await fetch(`${cleanHost}/metrics`);
```

**Risk:** If the backend doesn't validate, an attacker could make requests to internal resources.

**Recommendation:** Validate URLs against a whitelist of allowed hosts.

---

#### 15. Unsafe JSON parsing
**Severity:** MEDIUM
**Status:** Should fix
**Location:** `temper-view/src/api/gpuApi.ts:49`

**Issue:** Uses `JSON.parse()` on potentially malformed JSON with a regex fix for `inf/nan` values.

**Code:**
```typescript
const rawText = await res.text();
// Replace non-compliant numeric values with null
const safeJson = rawText.replace(/: ?(inf|-inf|nan)/gi, ': null');

return { host, data: JSON.parse(safeJson) };
```

**Risk:** If the backend returns invalid JSON, the app will crash.

**Recommendation:** Add proper try-catch and fallback handling.

---

#### 16. API Key Display Flaw
**Severity:** LOW
**Status:** Should fix
**Location:** `temper-view/src/components/portal/Portal.tsx:434`

**Issue:** API keys are displayed as `sk_ai_••••••••` which is a security oversight.

**Code:**
```typescript
<span className="font-mono bg-dark-800 px-1.5 py-0.5 rounded border border-dark-700">sk_ai_••••••••</span>
```

**Recommendation:** Never display partial API keys in the UI.

---

#### 17. Confirmation Dialog for Deletion
**Severity:** LOW
**Status:** Informational
**Location:** `temper-view/src/components/portal/Portal.tsx:350`

**Issue:** Uses browser's `confirm()` for destructive actions (deleting API keys).

**Risk:** `confirm()` can be bypassed in some contexts, and it blocks execution.

**Recommendation:** Implement custom confirmation dialogs with proper accessibility.

---

#### 18. Console.log in Production Code
**Severity:** LOW
**Status:** Should fix
**Location:** `temper-view/src/components/portal/Portal.tsx:35, 46`

**Issue:** Debug `console.log` statements should be removed or guarded with environment checks.

**Code:**
```typescript
console.log('Fetching profile for:', userId);
// ...
console.log('Profile loaded:', data);
```

**Recommendation:** Use environment variables to conditionally enable debug logging.

---

#### 19. iframe for Chat Interface
**Severity:** LOW
**Status:** Informational
**Location:** `temper-view/src/components/portal/Portal.tsx:720-725`

**Issue:** Uses an iframe to load `/chat/`. Ensure the iframe is properly sandboxed.

**Code:**
```typescript
<iframe
    src="/chat/"
    className="absolute inset-0 w-full h-full border-0"
    title="AI Chat"
/>
```

**Recommendation:** Add sandbox attribute to iframe:
```typescript
<iframe
    src="/chat/"
    className="absolute inset-0 w-full h-full border-0"
    title="AI Chat"
    sandbox="allow-same-origin allow-scripts allow-forms"
/>
```

---

## Summary Table

| Severity | Issue | Component | Location | Status |
|----------|-------|-----------|----------|--------|
| CRITICAL | Hardcoded Supabase anon key | temper-view | `supabase.ts:5` | ❌ Fix |
| CRITICAL | API key only checked client-side | temper | `MetricServer.cpp:276` | ❌ Fix |
| HIGH | Command injection via IPMI | temper | `IpmiController.cpp` | ❌ Fix |
| HIGH | No CORS restrictions | temper-view | Nginx config | ❌ Fix |
| HIGH | No Content Security Policy | temper-view | Nginx config | ❌ Fix |
| MEDIUM | Input validation on CLI args | temper | `main.cpp:54-61` | ⚠️ Fix |
| MEDIUM | JSON injection risk | temper | `MetricServer.cpp` | ⚠️ Fix |
| MEDIUM | No rate limiting on API | temper | `MetricServer.cpp` | ⚠️ Fix |
| MEDIUM | Unsafe JSON parsing | temper-view | `gpuApi.ts:49` | ⚠️ Fix |
| MEDIUM | XSS via alert() | temper-view | Multiple | ⚠️ Fix |
| MEDIUM | localStorage API key gen | temper-view | `Portal.tsx:324` | ⚠️ Review |
| MEDIUM | No input validation on hosts | temper-view | `SettingsPage.tsx:64` | ⚠️ Fix |
| LOW | Process info disclosure | temper | `NVMLManager.cpp` | ℹ️ Info |
| LOW | API key display | temper-view | `Portal.tsx:434` | ⚠️ Fix |
| LOW | Confirmation dialog | temper-view | `Portal.tsx:350` | ℹ️ Info |
| LOW | Console.log | temper-view | `Portal.tsx:35,46` | ⚠️ Fix |
| LOW | iframe security | temper-view | `Portal.tsx:720` | ℹ️ Info |

---

## Immediate Action Items

### URGENT (Fix within 24 hours)
1. [ ] Remove hardcoded Supabase keys from both temper and temper-view
2. [ ] Implement server-side API key validation
3. [ ] Restrict CORS to specific origins in Nginx

### HIGH (Fix within 1 week)
1. [ ] Add CSP headers to Nginx configuration
2. [ ] Validate and sanitize all IPMI command arguments
3. [ ] Add rate limiting to the metric server
4. [ ] Replace `alert()` with proper error components
5. [ ] Implement secure API key generation server-side

### MEDIUM (Fix within 2 weeks)
1. [ ] Add input validation on command-line arguments
2. [ ] Sanitize all string inputs before JSON serialization
3. [ ] Validate remote host URLs before making requests
4. [ ] Add proper try-catch for JSON parsing
5. [ ] Remove console.log statements or guard with environment checks

### LOW (Fix within 1 month)
1. [ ] Review process information disclosure
2. [ ] Implement custom confirmation dialogs
3. [ ] Add iframe sandbox attributes

---

## Testing Recommendations

1. **API Key Validation Test:**
   - Attempt requests without valid API key headers
   - Attempt requests with valid key from different origin
   - Test with malformed API key values

2. **Command Injection Test:**
   - Test IPMI commands with malicious input (quotes, semicolons, backticks)
   - Verify proper rejection of invalid inputs

3. **CORS Test:**
   - Test requests from different origins
   - Verify that unauthorized origins are blocked

4. **XSS Test:**
   - Attempt to inject scripts into GPU names and serial numbers
   - Verify proper sanitization

5. **Rate Limiting Test:**
   - Attempt to flood the metric server with connections
   - Verify proper blocking after threshold is reached

---

## References

- OWASP Top 10: https://owasp.org/www-project-top-ten/
- CWE-94: Improper Control of Generation of Code ('Code Injection')
- CWE-79: Improper Neutralization of Input During Web Page Generation ('Cross-site Scripting')
- CWE-352: Cross-Site Request Forgery (CSRF)
- CWE-22: Improper Limitation of a Pathname to a Restricted Directory ('Path Traversal')
