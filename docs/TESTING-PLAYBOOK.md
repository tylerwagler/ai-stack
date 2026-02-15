# Testing Playbook - ai-stack

**Last Updated:** 2026-02-02

This document provides step-by-step testing procedures for validating the ai-stack system, with special focus on the llama-server status dashboard and state transitions.

## Table of Contents

1. [Pre-Test Setup](#pre-test-setup)
2. [Authentication Testing](#authentication-testing)
3. [Status Transition Testing](#status-transition-testing)
4. [Progress Display Testing](#progress-display-testing)
5. [Load Testing](#load-testing)
6. [Error Recovery Testing](#error-recovery-testing)
7. [Integration Testing](#integration-testing)
8. [Performance Testing](#performance-testing)
9. [Automated Test Scripts](#automated-test-scripts)

## Pre-Test Setup

### Prerequisites

```bash
# Verify all services running
docker compose ps

# Verify environment variables loaded
export $(cat .env | grep -v '^#' | xargs)

# Verify network access
curl -s http://localhost:3000 > /dev/null && echo "✅ temper-view accessible"
curl -s http://localhost:3000/api/metrics > /dev/null && echo "✅ metrics accessible"

# Open browser to dashboard
xdg-open http://localhost:3000 || open http://localhost:3000
```

### Test Environment

- **Dashboard URL:** http://localhost:3000
- **Browser DevTools:** Open Network and Console tabs
- **Terminal:** Split screen with logs
- **Logs Command:** `docker compose logs -f fan-manager llama-server`

### Baseline Metrics

Record initial state:

```bash
curl -s http://localhost:3000/api/metrics | jq '.ai_service' > baseline.json
cat baseline.json
```

Expected baseline (model loaded):
```json
{
  "status": "ready",
  "load_progress": 1,
  "model": "model-name.gguf",
  "slots_used": 0,
  "slots_total": 4
}
```

---

## Authentication Testing

### Test 1: llama-server Endpoint Authentication

**Objective:** Verify all llama-server endpoints require LLAMA_API_KEY

**Procedure:**

1. Test without authentication:
```bash
curl -s http://localhost:8082/chat/health | jq .
```

**Expected:** `401 Unauthorized`
```json
{
  "error": {
    "message": "Invalid API Key",
    "type": "authentication_error",
    "code": 401
  }
}
```

2. Test with correct key:
```bash
curl -s -H "Authorization: Bearer $LLAMA_API_KEY" \
  http://localhost:8082/chat/health | jq .
```

**Expected:** `200 OK`
```json
{
  "status": "ok"
}
```

3. Test with wrong key:
```bash
curl -s -H "Authorization: Bearer sk-ant-wrong-key" \
  http://localhost:8082/chat/health | jq .
```

**Expected:** `401 Unauthorized`

✅ **Pass Criteria:** All endpoints block without auth, allow with correct key

---

### Test 2: temper Metrics Authentication

**Objective:** Verify METRICS_API_KEY protection

**Procedure:**

1. Test direct access without auth:
```bash
curl -s http://localhost:3001/metrics | jq .
```

**Expected:** `401 Unauthorized`

2. Test with correct key:
```bash
curl -s -H "X-API-Key: $METRICS_API_KEY" \
  http://localhost:3001/metrics | jq .ai_service
```

**Expected:** `200 OK` with metrics

3. Test nginx proxy (auto-injects key):
```bash
curl -s http://localhost:3000/api/metrics | jq .ai_service
```

**Expected:** `200 OK` with metrics (no auth header needed)

✅ **Pass Criteria:** Direct access requires key, nginx proxy works without user-provided key

---

### Test 3: User API Key Validation

**Objective:** Verify ai-proxy validates user keys against database

**Procedure:**

1. Get a valid user API key from database:
```bash
docker exec -it ai-supabase-db-1 psql -U postgres -d postgres -c \
  "SELECT key FROM api_keys WHERE revoked = FALSE LIMIT 1;"
```

2. Test with valid key:
```bash
USER_KEY="sk-ant-..."  # From step 1
curl -X POST http://localhost:8081/v1/chat/completions \
  -H "Authorization: Bearer $USER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-3-5-sonnet-20241022","messages":[{"role":"user","content":"Test"}],"max_tokens":10}'
```

**Expected:** `200 OK` with completion

3. Test with invalid key:
```bash
curl -X POST http://localhost:8081/v1/chat/completions \
  -H "Authorization: Bearer sk-ant-invalid-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-3-5-sonnet-20241022","messages":[{"role":"user","content":"Test"}]}'
```

**Expected:** `401 Unauthorized` or error message about invalid key

✅ **Pass Criteria:** Valid keys work, invalid keys rejected

---

## Status Transition Testing

### Test 4: OFFLINE → LOADING Transition

**Objective:** Verify status changes to LOADING when container starts

**Procedure:**

1. Stop llama-server:
```bash
docker compose stop llama-server
```

2. Watch dashboard - should show **Offline** (red badge)

3. Watch metrics in terminal:
```bash
watch -n 0.1 'curl -s http://localhost:3000/api/metrics | jq -r ".ai_service.status"'
```

4. Start llama-server:
```bash
docker compose start llama-server
```

5. Observe transition:
```
OFFLINE (red) → LOADING (yellow, should appear within 5-10 seconds)
```

6. Check logs:
```bash
docker compose logs -f fan-manager | grep -i "status\|loading"
```

**Expected Log Output:**
```
[Llama] Server loading, progress: 0.0%
[Llama] Model loading: model-name.gguf (0.0%)
```

✅ **Pass Criteria:**
- Status changes from OFFLINE to LOADING within 10 seconds of container start
- Dashboard shows "Loading" badge (yellow)
- Progress bar appears (pulsing or with percentage)

---

### Test 5: LOADING → READY Transition

**Objective:** Verify status changes to READY when model finishes loading

**Prerequisites:** Start from Test 4 (llama-server in LOADING state)

**Procedure:**

1. Continue watching dashboard from Test 4

2. Monitor progress updates:
```bash
while true; do
  curl -s http://localhost:3000/api/metrics | jq -r ".ai_service | {status, load_progress}"
  sleep 1
done
```

3. Observe:
- Progress bar should update (if llama.cpp supports it)
- Status should transition to READY when complete

4. Time the transition:
```bash
# Start timer when LOADING appears
# Stop when READY appears
# Typical: 60-90 seconds for cold start
```

**Expected Progression:**
```
LOADING (0.0%) → LOADING (0.1%) → ... → LOADING (0.887%) → READY (1.0)
```

✅ **Pass Criteria:**
- Status transitions to READY within 120 seconds
- Dashboard shows "Ready" badge (green)
- Progress bar disappears or shows 100%
- TPS metrics become visible
- Slot utilization shows "0/4 slots used"

---

### Test 6: READY → OFFLINE Transition

**Objective:** Verify status detects server crash/stop

**Prerequisites:** llama-server in READY state

**Procedure:**

1. Watch dashboard (should show READY)

2. Forcefully stop llama-server:
```bash
docker compose kill llama-server
```

3. Monitor transition timing:
```bash
time bash -c 'while curl -s http://localhost:3000/api/metrics | jq -r ".ai_service.status" | grep -q "ready"; do sleep 0.1; done; echo "OFFLINE detected"'
```

**Expected:** OFFLINE detected within 1.5 seconds

4. Check dashboard:
- Badge changes to "Offline" (red)
- Progress bar hidden
- TPS shows "—" or hidden
- Start button enabled (if admin)

✅ **Pass Criteria:**
- Status changes to OFFLINE within 2 seconds of server stop
- All metrics reset to zero/defaults
- Frontend gracefully handles state change (no crashes/errors)

---

### Test 7: Rapid Restart (OFFLINE → LOADING → READY)

**Objective:** Verify smooth transitions during restart

**Procedure:**

1. From READY state, restart llama-server:
```bash
docker compose restart llama-server
```

2. Watch full cycle:
```bash
watch -n 0.1 'curl -s http://localhost:3000/api/metrics | jq -r ".ai_service | {status, load_progress, slots_total}"'
```

3. Document timing:
- T+0s: READY → OFFLINE
- T+5s: OFFLINE → LOADING
- T+15s: LOADING → READY (warm start)

4. Verify no stuck states or flapping

✅ **Pass Criteria:**
- All transitions occur in expected order
- No extended periods (>5s) in unexpected states
- Model loads faster on warm start (15-30s vs 60-90s cold)

---

## Progress Display Testing

### Test 8: Indeterminate Progress Animation

**Objective:** Verify pulsing animation when progress = 0

**Procedure:**

1. Trigger model load (see Test 4)

2. When status = LOADING, observe progress bar

3. If load_progress = 0.0, should see:
- Pulsing cyan bar (full width)
- No percentage number displayed
- Smooth animation (not jittery)

4. Check CSS animation:
```javascript
// In browser DevTools console
document.querySelector('.animate-pulse')
// Should exist when progress = 0
```

✅ **Pass Criteria:**
- Pulsing animation visible
- No percentage text
- Smooth, continuous animation

---

### Test 9: Determinate Progress Display

**Objective:** Verify percentage display when llama.cpp reports progress

**Prerequisites:** llama.cpp build with load_progress support

**Procedure:**

1. Trigger cold start (clear VRAM first):
```bash
docker compose down
# Wait for full shutdown
docker compose up -d llama-server fan-manager
```

2. Watch for progress values:
```bash
while true; do
  progress=$(curl -s http://localhost:3000/api/metrics | jq -r ".ai_service.load_progress")
  echo "$(date +%H:%M:%S) - Progress: $progress"
  [ "$progress" = "1" ] && break
  sleep 0.5
done
```

3. On dashboard, verify:
- Progress bar fills left-to-right
- Percentage displayed (e.g., "42%")
- Updates smoothly (not jumping)

4. Check specific values appear:
```
0.0 → 0.157 → 0.314 → 0.471 → 0.628 → 0.785 → 0.887 → 1.0
```

✅ **Pass Criteria:**
- Progress values update (not stuck at 0)
- Percentage matches bar width
- Values increase monotonically (no decreases)
- Reaches 1.0 (100%) before status → READY

---

### Test 10: Progress Bar Visibility

**Objective:** Verify progress bar shows/hides correctly per state

**Procedure:**

Test each state:

| State | Expected Progress Bar |
|-------|----------------------|
| OFFLINE | Hidden |
| LOADING | Visible (pulsing or percentage) |
| READY | Hidden (or 100% then fade) |
| IDLE | Hidden |

**Verification:**
```bash
# Check each state
for state in offline loading ready idle; do
  echo "Testing state: $state"
  # Manually trigger state or wait for transition
  # Verify progress bar visibility
done
```

✅ **Pass Criteria:** Progress bar visibility matches table above

---

## Load Testing

### Test 11: Concurrent Request Impact

**Objective:** Verify status stays READY during inference load

**Prerequisites:** Model in READY state

**Procedure:**

1. Baseline: Record current metrics
```bash
curl -s http://localhost:3000/api/metrics | jq '.ai_service' > before_load.json
```

2. Generate load (10 concurrent requests):
```bash
USER_KEY="sk-ant-..."  # Valid user key

for i in {1..10}; do
  curl -X POST http://localhost:8081/v1/chat/completions \
    -H "Authorization: Bearer $USER_KEY" \
    -H "Content-Type: application/json" \
    -d '{"model":"claude-3-5-sonnet-20241022","messages":[{"role":"user","content":"Write a story"}],"max_tokens":500}' \
    > /dev/null 2>&1 &
done
```

3. Monitor status during load:
```bash
watch -n 0.1 'curl -s http://localhost:3000/api/metrics | jq -r ".ai_service | {status, slots_used, slots_total, tps: .predicted_tokens_seconds}"'
```

4. Verify:
- Status stays READY (no false LOADING)
- slots_used increases (1-4 used)
- TPS increases (>0 tokens/second)
- load_progress stays at 1.0

5. After load completes:
```bash
curl -s http://localhost:3000/api/metrics | jq '.ai_service' > after_load.json
```

6. Compare:
```bash
diff before_load.json after_load.json
# Should see: tokens_predicted_total increased, counters updated
```

✅ **Pass Criteria:**
- Status remains READY throughout
- Slot utilization updates correctly
- TPS metrics show throughput
- No status flapping or false transitions

---

### Test 12: Slot Saturation Behavior

**Objective:** Verify behavior when all slots busy

**Prerequisites:** Model with 4 slots

**Procedure:**

1. Generate exactly 4 long-running requests:
```bash
for i in {1..4}; do
  curl -X POST http://localhost:8081/v1/chat/completions \
    -H "Authorization: Bearer $USER_KEY" \
    -H "Content-Type: application/json" \
    -d '{"model":"claude-3-5-sonnet-20241022","messages":[{"role":"user","content":"Write a very long essay"}],"max_tokens":2000}' \
    > /dev/null 2>&1 &
done
```

2. Monitor slot usage:
```bash
watch -n 0.1 'curl -s http://localhost:3000/api/metrics | jq ".ai_service.slots_used, .ai_service.slots_total"'
```

**Expected:** "4/4 slots used"

3. Send 5th request (should queue):
```bash
time curl -X POST http://localhost:8081/v1/chat/completions \
  -H "Authorization: Bearer $USER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-3-5-sonnet-20241022","messages":[{"role":"user","content":"Hello"}],"max_tokens":10}'
```

4. Verify:
- 5th request waits for available slot
- requests_deferred increases (if metric available)
- No crash or error

✅ **Pass Criteria:**
- System handles slot saturation gracefully
- Queued requests complete eventually
- No status errors or crashes

---

## Error Recovery Testing

### Test 13: Authentication Failure Handling

**Objective:** Verify graceful handling of auth failures

**Procedure:**

1. Break LLAMA_API_KEY in fan-manager:
```bash
docker compose exec fan-manager sh -c "export LLAMA_API_KEY=wrong && killall temper"
```

2. Watch status:
```bash
watch -n 0.5 'curl -s http://localhost:3000/api/metrics | jq -r ".ai_service.status"'
```

**Expected:** Status → OFFLINE (auth failures cause health check to fail)

3. Check logs:
```bash
docker compose logs fan-manager | grep -i "401\|unauthorized"
```

4. Restore correct key:
```bash
docker compose restart fan-manager
```

5. Verify recovery:
- Status → LOADING or READY
- Metrics update normally

✅ **Pass Criteria:**
- Auth failure causes OFFLINE state (not crash)
- Logs show clear error messages
- System recovers after key fix

---

### Test 14: Network Interruption Recovery

**Objective:** Verify recovery from temporary network issues

**Procedure:**

1. Block traffic between temper and llama-server:
```bash
# Add iptables rule (may require privileged mode)
sudo iptables -I FORWARD -s $(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' fan-manager) -j DROP
```

2. Watch status change to OFFLINE:
```bash
watch -n 0.5 'curl -s http://localhost:3000/api/metrics | jq -r ".ai_service.status"'
```

3. After 10 seconds, restore connectivity:
```bash
sudo iptables -D FORWARD -s $(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' fan-manager) -j DROP
```

4. Verify automatic recovery:
- Status → READY (within 1-2 seconds)
- No manual intervention needed

✅ **Pass Criteria:**
- Detects network failure within 2 seconds
- Recovers automatically when connectivity restored
- No stuck state or manual restart required

---

### Test 15: Model File Corruption Recovery

**Objective:** Verify handling of model load failures

**Procedure:**

1. Stop llama-server:
```bash
docker compose stop llama-server
```

2. Corrupt model file (backup first):
```bash
docker volume inspect ai-stack_llama_cache
# Find mount point, backup model file
# Truncate or delete model file
```

3. Start llama-server:
```bash
docker compose start llama-server
```

4. Observe behavior:
- Status may show LOADING
- Eventually should show error in logs
- May stay LOADING indefinitely if no error detection

5. Check logs:
```bash
docker compose logs llama-server | grep -i "error\|failed"
```

6. Restore model file and restart

✅ **Pass Criteria:**
- System doesn't crash
- Clear error messages in logs
- Dashboard shows appropriate state (LOADING or OFFLINE)

---

## Integration Testing

### Test 16: End-to-End Data Flow

**Objective:** Trace data from llama-server through to dashboard display

**Procedure:**

1. Enable verbose logging:
```bash
docker compose exec fan-manager sh -c "export VERBOSE=1 && killall temper"
```

2. Open 4 terminal windows:
- Window 1: `docker compose logs -f llama-server`
- Window 2: `docker compose logs -f fan-manager`
- Window 3: `watch -n 0.1 'curl -s http://localhost:3000/api/metrics | jq ".ai_service.status"'`
- Window 4: Browser DevTools Network tab

3. Trigger state change (restart llama-server)

4. Trace data flow:
- llama-server logs: Model loading started
- fan-manager logs: "[Llama] Server loading, progress: X%"
- Terminal 3: Status changes reflected
- Browser: Network requests show updated metrics
- Dashboard: UI updates in real-time

5. Record timestamps at each stage:
- T0: llama-server starts model load
- T1: fan-manager detects LOADING
- T2: Metrics endpoint returns LOADING
- T3: Browser fetches updated metrics
- T4: React re-renders dashboard

**Expected Total Latency:** <500ms from T0 to T4

✅ **Pass Criteria:**
- Data flows through all layers correctly
- Latency is acceptable (<1 second)
- No data loss or corruption

---

### Test 17: Browser Refresh Persistence

**Objective:** Verify state persists across page reloads

**Procedure:**

1. With model in READY state, note current metrics

2. Refresh browser (F5 or Cmd+R)

3. Observe:
- Dashboard loads with current status (READY)
- Metrics immediately displayed (no blank state)
- Progress bars/animations correct

4. Test during LOADING state:
- Trigger model load
- Refresh browser mid-load
- Progress should resume from current value

✅ **Pass Criteria:**
- State persists (no reset to OFFLINE)
- Metrics fetch immediately on page load
- User sees correct state within 200ms

---

## Performance Testing

### Test 18: Polling Interval Accuracy

**Objective:** Verify 100ms polling interval is maintained

**Procedure:**

1. Monitor temper polling frequency:
```bash
docker compose logs -f fan-manager | grep -i "model loaded" | \
  while read line; do
    echo "$(date +%s.%N): $line"
  done
```

2. Calculate intervals between log entries:
```bash
# Should see entries roughly every 100ms (10 per second)
```

3. Measure over 60 seconds:
```bash
timeout 60 docker compose logs -f fan-manager | grep -c "Model loaded"
# Expected: ~600 (10 per second * 60 seconds)
```

4. Check for drift or blocking:
- Count should be 580-620 (some variance acceptable)
- No periods with zero updates (would indicate blocking)

✅ **Pass Criteria:**
- Polling occurs ~10 times per second
- No significant drift (variance <5%)
- No blocking or stalls

---

### Test 19: Frontend Refresh Rate

**Objective:** Verify React Query refetches every 100ms

**Procedure:**

1. Open browser DevTools → Network tab

2. Filter for "metrics" requests

3. Watch request timestamps over 10 seconds

4. Calculate average interval:
```
(Last request time - First request time) / Request count
```

**Expected:** ~100ms (10 requests per second)

5. Check for request failures:
- Should be all 200 OK
- No 401, 500, or timeout errors

✅ **Pass Criteria:**
- Average interval: 90-110ms
- All requests successful
- No visible lag in dashboard updates

---

### Test 20: Resource Usage Under Load

**Objective:** Verify system performance under continuous operation

**Procedure:**

1. Record baseline resource usage:
```bash
docker stats fan-manager llama-server --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"
```

2. Run for 10 minutes with continuous monitoring:
```bash
while true; do
  docker stats fan-manager llama-server --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"
  sleep 60
done > resource_usage.log
```

3. Generate inference load during test (optional)

4. Analyze results:
```bash
# Check for memory leaks (memory should be stable)
# Check CPU usage (should be reasonable)
cat resource_usage.log
```

**Expected:**
- fan-manager CPU: <5%
- fan-manager RAM: <100MB
- llama-server CPU: 0-100% (depends on load)
- llama-server RAM: Model size + overhead (stable, no leaks)

✅ **Pass Criteria:**
- No memory leaks (RAM stays constant)
- CPU usage appropriate for load
- No resource exhaustion

---

## Automated Test Scripts

### Script 1: Authentication Matrix Test

**Location:** `/temp/test-auth-matrix.sh`

**Usage:**
```bash
cd /temp
chmod +x test-auth-matrix.sh
./test-auth-matrix.sh
```

**Expected Output:** All endpoints show expected auth behavior

---

### Script 2: Load Progress Monitor

**Location:** `/temp/monitor-load-progress.sh`

**Usage:**
```bash
cd /temp
chmod +x monitor-load-progress.sh
./monitor-load-progress.sh &
MONITOR_PID=$!

# Trigger model load
docker compose restart llama-server

# Wait for completion
wait $MONITOR_PID

# Review logs
cat load-progress-log.jsonl
cat load-progress-summary.txt
```

**Output:** Detailed timing and progress value logs

---

### Script 3: Status Consistency Test

**Create:**
```bash
cat > /temp/consistency-test.sh <<'EOF'
#!/bin/bash
# Test load/unload consistency over 10 cycles

CYCLES=10
FAILURES=0

for i in $(seq 1 $CYCLES); do
  echo "=== Cycle $i/$CYCLES ==="

  # Stop server
  docker compose stop llama-server
  sleep 2

  # Verify OFFLINE
  status=$(curl -s http://localhost:3000/api/metrics | jq -r '.ai_service.status')
  [ "$status" = "offline" ] || { echo "❌ Failed to detect OFFLINE"; ((FAILURES++)); }

  # Start server
  docker compose start llama-server
  sleep 5

  # Wait for READY (up to 120 seconds)
  timeout=120
  while [ $timeout -gt 0 ]; do
    status=$(curl -s http://localhost:3000/api/metrics | jq -r '.ai_service.status')
    [ "$status" = "ready" ] && break
    sleep 1
    ((timeout--))
  done

  [ "$status" = "ready" ] || { echo "❌ Failed to reach READY"; ((FAILURES++)); }
  echo "✅ Cycle $i completed"
done

echo "=== Results ==="
echo "Cycles: $CYCLES"
echo "Failures: $FAILURES"
[ $FAILURES -eq 0 ] && echo "✅ All tests passed" || echo "❌ Some tests failed"
EOF

chmod +x /temp/consistency-test.sh
```

**Usage:**
```bash
/temp/consistency-test.sh
```

**Expected:** 0 failures over 10 cycles

---

## Test Results Template

### Test Report Format

```markdown
## Test Report: [Test Name]

**Date:** YYYY-MM-DD
**Tester:** [Name]
**Environment:** Docker Compose / ai-stack v1.0

### Test Results

| Test ID | Test Name | Status | Notes |
|---------|-----------|--------|-------|
| 1 | llama-server Auth | ✅ PASS | All endpoints blocked without key |
| 2 | temper Metrics Auth | ✅ PASS | nginx proxy works correctly |
| 4 | OFFLINE → LOADING | ✅ PASS | Transition in 5.2s |
| 5 | LOADING → READY | ✅ PASS | Cold start: 68s |
| 8 | Indeterminate Progress | ✅ PASS | Pulsing animation visible |
| 11 | Concurrent Load | ✅ PASS | Status stayed READY, TPS: 24.5 |
| ... | ... | ... | ... |

### Issues Found

1. **Issue:** Progress sometimes stuck at 0%
   - **Severity:** Low
   - **Workaround:** Indeterminate animation still works
   - **Root Cause:** llama.cpp build doesn't expose progress

### Performance Metrics

- **Cold Start Time:** 68 seconds
- **Warm Start Time:** 12 seconds
- **Status Detection Latency:** 120ms average
- **Polling Accuracy:** 99.8% (598/600 polls in 60s)

### Recommendations

1. Upgrade llama.cpp to version with load_progress support
2. Consider adding status transition logging to frontend
3. Monitor for status flapping in production
```

---

## Continuous Integration

### CI Test Suite

For automated testing in CI/CD:

```yaml
# .github/workflows/test.yml
name: Integration Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2

      - name: Start services
        run: docker compose up -d

      - name: Wait for healthy
        run: |
          timeout 120 bash -c 'until docker compose ps | grep -q "healthy"; do sleep 1; done'

      - name: Run auth tests
        run: ./temp/test-auth-matrix.sh

      - name: Run consistency tests
        run: ./temp/consistency-test.sh

      - name: Check metrics endpoint
        run: |
          curl -f http://localhost:3000/api/metrics

      - name: Collect logs
        if: failure()
        run: docker compose logs > test-logs.txt

      - name: Upload logs
        if: failure()
        uses: actions/upload-artifact@v2
        with:
          name: test-logs
          path: test-logs.txt
```

---

## See Also

- [API-REFERENCE.md](./API-REFERENCE.md) - Complete API documentation
- [AUTHENTICATION.md](./AUTHENTICATION.md) - Authentication guide
- [STATUS-STATES.md](./STATUS-STATES.md) - State machine documentation
- [../CLAUDE.md](../CLAUDE.md) - Development guide
- [../scripts/integration-test.sh](../scripts/integration-test.sh) - Integration test script
