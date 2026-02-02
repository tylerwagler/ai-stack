# Status States - AI Service State Machine

**Last Updated:** 2026-02-02

This document describes the state machine for the AI service (llama-server) status tracking in the ai-stack system.

## Table of Contents

1. [Overview](#overview)
2. [State Definitions](#state-definitions)
3. [State Transitions](#state-transitions)
4. [State Detection Logic](#state-detection-logic)
5. [Frontend Display](#frontend-display)
6. [Timing Characteristics](#timing-characteristics)
7. [Troubleshooting](#troubleshooting)

## Overview

The AI service status is determined by temper's LlamaMonitor component, which polls llama-server every 100ms and analyzes the responses to determine the current state.

### State Enum

```cpp
enum class LlamaStatus {
    OFFLINE,  // Server not responding
    LOADING,  // Model loading in progress
    READY,    // Model loaded, accepting requests
    IDLE,     // Server running, no model loaded
    ERROR     // Error state (currently unused)
};
```

### State Flow Diagram

```
┌─────────┐
│ OFFLINE │ ◄──────────────────────────────────┐
└────┬────┘                                    │
     │                                         │
     │ /health responds                        │
     │ /v1/models 503                          │
     │                                         │
     ▼                                         │
┌─────────┐                                    │
│ LOADING │                                    │
└────┬────┘                                    │
     │                                         │
     │ /v1/models 200 OK                       │
     │ model found                             │
     │                                         │
     ▼                                         │
┌─────────┐                                    │
│  READY  │ ───────────────────────────────────┤
└────┬────┘    /health fails                   │
     │                                         │
     │ model unloaded                          │
     │ (router mode only)                      │
     │                                         │
     ▼                                         │
┌─────────┐                                    │
│  IDLE   │ ───────────────────────────────────┘
└─────────┘    /health fails
```

## State Definitions

### OFFLINE

**Meaning:** llama-server is not responding or unreachable

**Detected When:**
- `/chat/health` request fails (timeout, connection refused, or HTTP error)
- Container is stopped or crashed
- Network connectivity issues between temper and llama-server

**Metrics Behavior:**
- All metrics reset to zero/default values
- `load_progress = 0.0`
- `modelName = "Unknown"`
- `slotsUsed = 0`, `slotsTotal = 0`
- All performance metrics (`prompt_tokens_seconds`, etc.) reset

**Frontend Display:**
- Badge: Red "Offline"
- Progress bar: Hidden
- TPS/metrics: Hidden or showing "—"
- Start button: Enabled (if admin)

**Expected Duration:**
- Cold start: 5-10 seconds until first /health response
- Crash recovery: Depends on Docker restart policy

**Log Output:**
```
[Llama] Health check failed: Connection refused
[Llama] Status: OFFLINE
```

---

### LOADING

**Meaning:** Model is being loaded into VRAM

**Detected When:**
- `/chat/health` returns 200 OK (server is alive)
- `/chat/v1/models` returns non-zero exit code (typically 503 Service Unavailable)
- OR (router mode): `/chat/v1/models` shows `"status": {"value": "loading"}`

**Metrics Behavior:**
- `load_progress`:
  - `0.0` if not available (indeterminate)
  - `0.0 - 1.0` if reported by llama.cpp (actual progress)
- `modelName = "Loading..."` (or model name if known)
- `slotsUsed = 0`, `slotsTotal = 0`
- Performance metrics stay at zero

**Progress Data Sources:**

1. **Router mode:** Extract from `/chat/v1/models` response
```json
{
  "models": [{
    "id": "model.gguf",
    "status": {
      "value": "loading",
      "load_progress": 0.573
    }
  }]
}
```

2. **Non-router mode:** Extract from `/chat/health` response
```json
{
  "status": "loading",
  "load_progress": 0.42
}
```

**Frontend Display:**
- Badge: Yellow "Loading"
- Progress bar: Visible
  - Pulsing animation if `progress = 0` (indeterminate)
  - Percentage display if `progress > 0` (determinate)
- TPS/metrics: Hidden or "Loading..."
- Start button: Disabled

**Expected Duration:**
- Cold start (first load): 60-90 seconds for 30B model
- Warm start (cached): 5-15 seconds
- Depends on: Model size, GPU count, VRAM speed, PCIE bandwidth

**Log Output:**
```
[Llama] Server loading, progress: 42.5%
[Llama] Model loading: model-name.gguf (42.5%)
```

---

### READY

**Meaning:** Model is fully loaded and ready to accept inference requests

**Detected When:**
- `/chat/health` returns 200 OK
- `/chat/v1/models` returns 200 OK with model data
- **Router mode:** Model status shows `"value": "loaded"` or `"value": "ready"`
- **Non-router mode:** `models` or `data` array contains at least one model

**Metrics Behavior:**
- `load_progress = 1.0` (100%)
- `modelName`: Full model filename (e.g., `"model-name.gguf"`)
- `slotsUsed`: Current number of busy slots (0-4 typically)
- `slotsTotal`: Total available slots (e.g., 4)
- Performance metrics actively updated:
  - `prompt_tokens_seconds`: Prompt processing throughput
  - `predicted_tokens_seconds`: Token generation throughput (TPS)
  - `kv_cache_usage_ratio`: KV cache utilization (0.0-1.0)
  - `requests_processing`: Active inference requests

**Slot States:**
- `idle`: Slot available for new request
- `processing`: Slot actively generating tokens
- Each slot tracked individually with KV cache metrics

**Frontend Display:**
- Badge: Green "Ready"
- Progress bar: Hidden (or showing 100%)
- TPS: Showing current throughput (e.g., "25.3 t/s")
- Slot utilization: "2/4 slots used"
- KV cache: "15% used (19,660 tokens)"
- Stop button: Enabled (if admin)

**Expected Duration:**
- Stable state during normal operation
- Remains READY until model unloaded or server crashes

**Log Output:**
```
[Llama] Model loaded: model-name.gguf
[Llama] Parsed 4 slots
[Llama] TPS: 25.3 tokens/s
```

---

### IDLE

**Meaning:** llama-server is running but no model is loaded

**Detected When:**
- `/chat/health` returns 200 OK
- `/chat/v1/models` returns 200 OK but no models found
- `models` and `data` arrays are empty or missing

**Metrics Behavior:**
- `load_progress = 0.0`
- `modelName = "None"`
- `slotsUsed = 0`, `slotsTotal = 0`
- All performance metrics at zero

**Frontend Display:**
- Badge: Gray "Idle"
- Progress bar: Hidden
- TPS: "—" (not applicable)
- Start button: Enabled (if admin)

**Expected Duration:**
- Immediately after server start (before first model load)
- After model is manually unloaded (router mode)
- Rare in single-model deployments

**Log Output:**
```
[Llama] Server running, no model loaded
[Llama] Status: IDLE
```

**Note:** Most deployments use single-model mode and skip IDLE (go directly from LOADING to READY).

---

### ERROR (Unused)

**Meaning:** Reserved for future error states

**Currently:** Not actively used in production code

**Potential Future Uses:**
- Model load failure (OOM, corrupted file)
- Server in error state but still responding
- Partial failure (some models loaded, others failed)

---

## State Transitions

### Transition Matrix

| From | To | Condition | Duration |
|------|-----|-----------|----------|
| OFFLINE | LOADING | `/health` passes, `/models` 503 | ~100ms |
| OFFLINE | READY | Server was stopped mid-operation | ~100ms |
| LOADING | READY | `/models` returns 200 with data | 5-90s |
| LOADING | OFFLINE | Server crashes during load | Instant |
| READY | OFFLINE | Server crashes or stops | 1-2s (timeout) |
| READY | LOADING | Model switch initiated (router mode) | Instant |
| READY | IDLE | Model unloaded (router mode) | ~100ms |
| IDLE | LOADING | Model load initiated | ~100ms |
| IDLE | OFFLINE | Server crashes | 1-2s (timeout) |

### Critical Transitions

#### OFFLINE → LOADING

**Trigger:** Docker container starts, llama-server begins initializing

**Detection:**
1. `/chat/health` begins responding (previously failed)
2. `/chat/v1/models` returns 503 (model not ready yet)

**Code Path (LlamaMonitor.cpp:245-251):**
```cpp
if (modelsRes.first != 0) {
    // Failed to get models (timeout or error, typically 503 during loading).
    m.status = LlamaStatus::LOADING;
    m.modelName = "Loading...";
    std::string healthJson = healthRes.second;
    double progress = extractJsonNumber(healthJson, "load_progress", 0, 0.0);
    m.load_progress = progress;
    return;
}
```

**Common Issues:**
- Health endpoint responds but models endpoint times out → LOADING detected correctly
- Both fail → Stays OFFLINE (correct behavior)

---

#### LOADING → READY

**Trigger:** Model finishes loading into VRAM

**Detection:**
1. `/chat/v1/models` now returns 200 OK
2. Response contains model in `models` or `data` array
3. Router mode: `status.value = "loaded"`

**Code Path (LlamaMonitor.cpp:308-342):**

**Router Mode:**
```cpp
if (statusValue == "loaded" || statusValue == "ready") {
    foundLoaded = true;
    detectedId = candidateId;
    break;
}
```

**Non-Router Mode:**
```cpp
if (modelsArrayPos != std::string::npos || dataArrayPos != std::string::npos) {
    // Extract first model's name
    m.status = LlamaStatus::READY;
    m.modelName = modelFile;
    m.load_progress = 1.0;
    foundLoaded = true;
}
```

**Common Issues:**
- Model name not extracted correctly → Shows "Unknown" but status correct
- Router mode not detected → Falls back to non-router mode detection

---

#### READY → OFFLINE

**Trigger:** Server crashes, container stops, or network failure

**Detection:**
1. `/chat/health` begins failing (timeout or connection refused)
2. Next poll cycle detects failure

**Code Path (LlamaMonitor.cpp:234-240):**
```cpp
if (healthRes.first != 0) {
    // Health check failed
    m = LlamaMetrics{};  // Reset all metrics
    m.status = LlamaStatus::OFFLINE;
    m.modelName = "Unknown";
}
```

**Timing:**
- Health endpoint timeout: 1 second
- Next poll cycle: 100ms after timeout
- Total detection time: ~1.1 seconds

**Common Issues:**
- Brief network glitch → False OFFLINE state (recovers next cycle)
- Container restart → Expected behavior

---

## State Detection Logic

### Polling Loop (Every 100ms)

```cpp
void LlamaMonitor::pollLoop() {
    while (running_) {
        auto startTime = std::chrono::steady_clock::now();

        // 1. Poll llama-server
        updateMetrics();

        // 2. Calculate elapsed time
        auto endTime = std::chrono::steady_clock::now();
        auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
            endTime - startTime
        ).count();

        // 3. Sleep for remainder of 100ms interval
        int sleepMs = 100 - elapsed;
        if (sleepMs > 0) {
            std::this_thread::sleep_for(std::chrono::milliseconds(sleepMs));
        }
    }
}
```

### State Decision Tree

```
START
  │
  ├─ GET /chat/health (timeout: 1s)
  │
  ├─ SUCCESS?
  │  ├─ YES
  │  │  │
  │  │  ├─ GET /chat/v1/models (timeout: 10s)
  │  │  │
  │  │  ├─ SUCCESS?
  │  │  │  ├─ YES
  │  │  │  │  │
  │  │  │  │  ├─ Parse JSON for models
  │  │  │  │  │
  │  │  │  │  ├─ Router mode status found?
  │  │  │  │  │  ├─ YES
  │  │  │  │  │  │  ├─ status.value = "loading" → LOADING
  │  │  │  │  │  │  └─ status.value = "loaded" → READY
  │  │  │  │  │  │
  │  │  │  │  │  └─ NO
  │  │  │  │  │     │
  │  │  │  │  │     ├─ Non-router mode models/data array found?
  │  │  │  │  │     │  ├─ YES → READY
  │  │  │  │  │     │  └─ NO → IDLE
  │  │  │  │  │
  │  │  │  │  └─ GET /chat/slots, /chat/metrics (if READY)
  │  │  │  │
  │  │  │  └─ NO (503/timeout)
  │  │  │     │
  │  │  │     └─ Extract load_progress from /health → LOADING
  │  │  │
  │  │  └─ DONE
  │  │
  │  └─ NO
  │     │
  │     └─ Reset metrics → OFFLINE
  │
  └─ DONE
```

### Timeout Values

| Endpoint | Timeout | Reason |
|----------|---------|--------|
| `/chat/health` | 1s | Fast check, localhost only |
| `/chat/v1/models` | 10s | May be slow during heavy load |
| `/chat/slots` | 10s | May be slow with many slots |
| `/chat/metrics` | 10s | May be slow with many counters |

**Why different timeouts?**
- Health is latency-sensitive (determines OFFLINE quickly)
- Other endpoints can be slower without impacting status detection

---

## Frontend Display

### Badge Component

```typescript
const getStatusBadge = (status: string) => {
  switch (status) {
    case 'ready':
      return <Badge variant="success">Ready</Badge>;  // Green
    case 'loading':
      return <Badge variant="warning">Loading</Badge>; // Yellow
    case 'offline':
      return <Badge variant="error">Offline</Badge>;   // Red
    case 'idle':
      return <Badge variant="secondary">Idle</Badge>;  // Gray
    default:
      return <Badge variant="secondary">Unknown</Badge>;
  }
};
```

### Progress Bar

```typescript
<ProgressBar
  progress={loadProgress}  // 0.0 - 1.0
  label={isLoading ? "Loading model..." : undefined}
/>
```

**Behavior:**
- `progress = 0`: Pulsing animation (indeterminate)
- `progress > 0 && < 1`: Percentage bar with number
- `progress = 1`: 100% (usually hidden when READY)

### Conditional Display

```typescript
{stats?.status === 'ready' && (
  <>
    <ThroughputMetric tps={stats.predicted_tokens_seconds} />
    <SlotUtilization used={stats.slots_used} total={stats.slots_total} />
    <KVCacheDisplay usage={stats.kv_cache_usage_ratio} />
  </>
)}

{stats?.status === 'loading' && (
  <ProgressBar progress={stats.load_progress} label="Loading model..." />
)}

{(stats?.status === 'offline' || stats?.status === 'idle') && (
  <StartButton onClick={handleStart} disabled={!isAdmin} />
)}
```

---

## Timing Characteristics

### State Transition Timings (Typical)

| Transition | Minimum | Typical | Maximum | Notes |
|------------|---------|---------|---------|-------|
| OFFLINE → LOADING | 100ms | 5s | 30s | Container startup time |
| LOADING → READY (cold) | 10s | 60s | 120s | Full model load |
| LOADING → READY (warm) | 2s | 10s | 30s | Model in VRAM |
| READY → OFFLINE | 100ms | 1.1s | 2s | Health timeout + poll |
| READY → LOADING (switch) | 100ms | 100ms | 500ms | Router mode only |

### Poll Cycle Breakdown

```
┌───────────────────────────────────────────────────────────┐
│ Single Poll Cycle (Target: 100ms)                        │
├───────────────────────────────────────────────────────────┤
│ 1. GET /health               │ 1-5ms   (localhost)       │
│ 2. GET /v1/models             │ 1-10ms  (if successful)   │
│ 3. GET /slots                 │ 1-5ms   (if READY)        │
│ 4. GET /metrics               │ 1-5ms   (if READY)        │
│ 5. Parse responses            │ 1-2ms   (string parsing)  │
│ 6. Update metrics struct      │ <1ms    (memory copy)     │
│ 7. Sleep remainder            │ 82-94ms (to hit 100ms)    │
└───────────────────────────────────────────────────────────┘
```

**Under Load:**
- Endpoints may respond slower (20-50ms each)
- Total cycle time stays at 100ms (polling interval fixed)
- If requests exceed 100ms → Next cycle starts immediately

### Frontend Refresh Rate

```typescript
const { data: metrics } = useQuery({
  queryKey: ['gpu-stats'],
  queryFn: fetchGPUMetrics,
  refetchInterval: 100,  // 100ms
});
```

**End-to-End Latency:**
- Backend poll: 100ms interval
- Network (localhost): <1ms
- React render: 16ms (60fps)
- **Total:** Status change visible within ~120ms

---

## Troubleshooting

### Status Stuck on LOADING

**Symptoms:**
- Dashboard shows "Loading" for >2 minutes
- Progress bar stuck at 0% or specific percentage

**Diagnosis:**

1. Check llama-server logs:
```bash
docker compose logs -f llama-server | tail -50
```

2. Look for errors:
- OOM (Out of Memory)
- CUDA errors
- File not found (model file missing)

3. Check model loading manually:
```bash
curl -H "Authorization: Bearer $LLAMA_API_KEY" \
  http://localhost:8082/chat/v1/models | jq .
```

**Common Causes:**
- Model file corrupted or missing
- Insufficient VRAM
- CUDA driver issue
- Model config error in models.ini

---

### Status Flapping (READY ↔ OFFLINE)

**Symptoms:**
- Status rapidly alternating between READY and OFFLINE
- Dashboard "flashing" between states

**Diagnosis:**

1. Check network stability:
```bash
ping -c 10 llama-server
```

2. Check llama-server health:
```bash
docker compose ps llama-server  # Should show "healthy"
```

3. Monitor response times:
```bash
time curl -H "Authorization: Bearer $LLAMA_API_KEY" \
  http://localhost:8082/chat/health
```

**Common Causes:**
- Server overloaded (high inference load)
- Network instability
- Container resource limits (CPU throttling)
- Health check timeout too aggressive

**Solutions:**
- Increase health timeout (currently 1s)
- Add request debouncing in frontend
- Reduce inference load
- Increase container resources

---

### Progress Never Updates (Stuck at 0%)

**Symptoms:**
- Status shows LOADING
- Progress bar shows pulsing animation (indeterminate)
- Never transitions to actual percentage

**Diagnosis:**

1. Check if llama.cpp fork supports load_progress:
```bash
curl -H "Authorization: Bearer $LLAMA_API_KEY" \
  http://localhost:8082/chat/health | jq .
```

2. Check temper logs for progress updates:
```bash
docker exec fan-manager sh -c "export VERBOSE=1 && killall temper"
docker compose logs -f fan-manager | grep -i progress
```

**Likely Cause:**
- llama.cpp build doesn't expose `load_progress` in `/health`
- Custom fork or old version without this feature

**Workaround:**
- Indeterminate progress is acceptable (shows activity)
- Upgrade to latest llama.cpp with progress support

---

### Status Shows IDLE Instead of READY

**Symptoms:**
- Model is loaded (inference works)
- Dashboard shows "Idle" status
- Metrics show no model name

**Diagnosis:**

1. Check /v1/models response format:
```bash
curl -H "Authorization: Bearer $LLAMA_API_KEY" \
  http://localhost:8082/chat/v1/models | jq .
```

2. Check non-router mode detection:
```bash
# Should see "models" or "data" array
curl -H "Authorization: Bearer $LLAMA_API_KEY" \
  http://localhost:8082/chat/v1/models | jq '.models, .data'
```

**Common Causes:**
- Unexpected JSON format from llama.cpp
- Model name in non-standard field
- Router mode enabled but not detected

**Solution:**
- Update LlamaMonitor.cpp parsing logic
- Check llama.cpp version compatibility

---

## See Also

- [API-REFERENCE.md](./API-REFERENCE.md) - llama-server endpoint documentation
- [AUTHENTICATION.md](./AUTHENTICATION.md) - API authentication guide
- [TESTING-PLAYBOOK.md](./TESTING-PLAYBOOK.md) - Testing status transitions
- [../temper/src/LlamaMonitor.cpp](../temper/src/LlamaMonitor.cpp) - Status detection implementation
- [../temper-view/src/components/charts/LlamaCppCard.tsx](../temper-view/src/components/charts/LlamaCppCard.tsx) - Frontend status display
