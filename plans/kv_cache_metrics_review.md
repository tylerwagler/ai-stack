# Detailed Review: llama.cpp KV Cache Metrics Implementation

## Executive Summary

You successfully added **global KV cache metrics** in two commits that provide foundational monitoring capabilities. The implementation is clean, well-architected, and handles multiple memory types. However, the current metrics provide only **aggregate statistics** across all slots. For detailed monitoring of individual request behavior, **per-slot metrics** need to be exposed.

---

## Analysis of Implemented Changes

### Commit 9e6eafdc8: "server : implement missing kv_cache metrics"

#### What Was Added

**New Public API Functions** (include/llama.h:534-540):
```cpp
// Get the number of used KV cache cells (i.e. have at least one sequence assigned to them)
// Returns -1 if the context does not use a KV cache
LLAMA_API int32_t llama_get_kv_cache_used_cells(const struct llama_context * ctx);

// Get the total number of KV cache cells
// Returns -1 if the context does not use a KV cache
LLAMA_API int32_t llama_get_kv_cache_token_count(const struct llama_context * ctx);
```

**Core Implementation** (src/llama-kv-cache.cpp:990-995):
- Added `get_used_cells()` method to `llama_kv_cache` class
- Aggregates usage across all cache streams
- Calculates total occupied cells (cells with at least one sequence assigned)

**Server Integration** (tools/server/server-context.cpp:1783-1785):
```cpp
// Collect KV cache metrics
res->kv_cache_used_cells = llama_get_kv_cache_used_cells(ctx);
res->kv_cache_tokens     = llama_get_kv_cache_token_count(ctx);
```

**Metrics Endpoint** (tools/server/server-context.cpp:3284-3291):
Exposes two new metrics via `/metrics`:
- `kv_cache_usage_ratio` - Percentage of cache utilized (0.0-1.0)
- `kv_cache_tokens` - Total KV cache capacity

**Strengths:**
✅ Clean API design with proper error handling (-1 for non-KV contexts)
✅ Minimal performance overhead (uses existing tracking)
✅ Well-documented with clear function comments
✅ Proper integration into metrics collection pipeline
✅ Added corresponding fields to `server_task_result_metrics` structure

---

### Commit b048c4e95: "context : add support for hybrid and iSWA memory types"

#### What Was Extended

**Problem Solved:**
Original implementation only handled base `llama_kv_cache` type. Modern models use:
- `llama_kv_cache_iswa` - Indirect Sequence Window Attention with base + SWA caches
- `llama_memory_hybrid` - Hybrid KV + recurrent memory
- `llama_memory_hybrid_iswa` - Hybrid with iSWA support

**Implementation** (src/llama-context.cpp:3126-3165):
Extended both functions with `dynamic_cast` cascade:

```cpp
int32_t llama_get_kv_cache_used_cells(const llama_context * ctx) {
    llama_memory_t mem = ctx->get_memory();
    if (!mem) return -1;

    // Try llama_kv_cache (simple KV cache)
    if (const llama_kv_cache * kv = dynamic_cast<const llama_kv_cache *>(mem)) {
        return kv->get_used_cells();
    }

    // Try llama_kv_cache_iswa (KV cache with sliding window attention)
    if (const llama_kv_cache_iswa * kv_iswa = dynamic_cast<const llama_kv_cache_iswa *>(mem)) {
        uint32_t total_used = 0;
        if (const llama_kv_cache * base = kv_iswa->get_base()) {
            total_used += base->get_used_cells();
        }
        if (const llama_kv_cache * swa = kv_iswa->get_swa()) {
            total_used += swa->get_used_cells();
        }
        return total_used;
    }

    // Try llama_memory_hybrid (hybrid KV + recurrent)
    if (const llama_memory_hybrid * hybrid = dynamic_cast<const llama_memory_hybrid *>(mem)) {
        if (const llama_kv_cache * kv = hybrid->get_mem_attn()) {
            return kv->get_used_cells();
        }
    }

    // Try llama_memory_hybrid_iswa (hybrid with iSWA)
    if (const llama_memory_hybrid_iswa * hybrid_iswa = dynamic_cast<const llama_memory_hybrid_iswa *>(mem)) {
        if (const llama_kv_cache_iswa * kv_iswa = hybrid_iswa->get_mem_attn()) {
            uint32_t total_used = 0;
            if (const llama_kv_cache * base = kv_iswa->get_base()) {
                total_used += base->get_used_cells();
            }
            if (const llama_kv_cache * swa = kv_iswa->get_swa()) {
                total_used += swa->get_used_cells();
            }
            return total_used;
        }
    }

    // Not a KV cache type (e.g., llama_memory_recurrent)
    return -1;
}
```

**Architectural Coverage:**
- ✅ Standard KV cache (Transformer models)
- ✅ iSWA cache (Models with sliding window attention)
- ✅ Hybrid memory (Models with both attention and recurrence)
- ✅ Hybrid iSWA (Advanced architectures combining both)
- ✅ Graceful degradation for recurrent-only models (returns -1)

**Strengths:**
✅ Comprehensive memory type support
✅ Properly aggregates multi-cache architectures (base + SWA)
✅ Consistent error handling across all types
✅ Future-proof against new memory architectures

---

## Current Metrics Capabilities

### What You Can Monitor Now

1. **Global Cache Utilization**
   - Total cells used across all active slots
   - Total cache capacity
   - Usage ratio (used/total)

2. **Architecture Detection**
   - Automatically adapts to model memory type
   - Works with standard, iSWA, hybrid, and hybrid-iSWA models

### What's Available Per-Slot in Code But Not Exposed

The `server_slot` structure (server-context.cpp:48-166) already tracks:

| Metric | Field | Line | Purpose |
|--------|-------|------|---------|
| **Cached tokens** | `n_prompt_tokens_cache` | 77 | Tokens successfully cached for reuse |
| **Prompt tokens** | `n_prompt_tokens_processed` | 78 | Total tokens in prompt processed |
| **Generated tokens** | `n_decoded` | 73 | Tokens generated so far |
| **Prompt time** | `t_prompt_processing` | 158 | Time spent processing prompt (ms) |
| **Generation time** | `t_token_generation` | 159 | Time spent generating (ms) |
| **Draft tokens total** | `n_draft_total` | 164 | Speculative decoding draft count |
| **Draft accepted** | `n_draft_accepted` | 165 | Accepted speculative tokens |
| **Last used time** | `t_last_used` | 68 | Timestamp of last activity |
| **Context size** | `n_ctx` | 71 | Per-slot context window size |

These are already computed and stored. The `get_timings()` method (line 323-344) calculates:
- `prompt_per_second` = tokens/s during prompt processing
- `predicted_per_second` = tokens/s during generation
- `draft_n_accepted` / `draft_n` = speculative acceptance rate

### Current `/slots` Endpoint Output

From `to_json()` method (line 414-450):
```json
{
  "id": 0,
  "n_ctx": 65536,
  "speculative": true,
  "state": "generating",
  "prompt_n": 1024,
  "prompt_ms": 245.3,
  "predicted_n": 128,
  "predicted_ms": 1024.5,
  "cache_n": 400
}
```

**Missing KV Cache Details:**
- Position range in cache (`pos_min`, `pos_max`)
- Cells allocated to this slot
- Fragmentation metrics
- Cache efficiency ratio
- Context window utilization

---

## What's Missing for Production Monitoring

### Priority 1: Per-Slot KV Cache Position Metrics

**Problem:** You can see global cache usage, but not WHERE each slot is positioned in the cache or HOW MUCH space each occupies.

**Available API Functions:**
```cpp
// include/llama.h:757-766
LLAMA_API llama_pos llama_memory_seq_pos_min(llama_memory_t mem, llama_seq_id seq_id);
LLAMA_API llama_pos llama_memory_seq_pos_max(llama_memory_t mem, llama_seq_id seq_id);
```

**What This Enables:**
- **Position range**: [pos_min, pos_max] shows which part of cache the slot occupies
- **Cells used**: (pos_max - pos_min + 1) gives slot's cache footprint
- **Fragmentation**: Gaps between slots indicate inefficiency
- **Context utilization**: pos_max / n_ctx shows how full the slot's window is

**Example Use Case:**
```
Slot 0: pos [0-1024]     → Using 1024 cells
Slot 1: pos [2048-3072]  → Using 1024 cells, but gap at [1025-2047] = fragmentation
Slot 2: pos [-1,-1]      → Empty (idle)
```

### Priority 2: Per-Slot Performance Metrics

**Problem:** Global metrics don't show if one slow request is blocking others.

**Already Computed in `get_timings()`:**
- `prompt_per_second` (line 330)
- `predicted_per_second` (line 335)
- `draft_n_accepted / draft_n` (lines 338-341)

**What This Enables:**
- Identify slow slots (low tokens/sec)
- Detect speculative decoding efficiency
- Find cache misses (low `cache_n` / `prompt_n` ratio)

### Priority 3: Cache Efficiency Metrics

**Formula:**
```
cache_efficiency = n_prompt_tokens_cache / n_prompt_tokens_processed
```

**Interpretation:**
- 1.0 = Perfect cache hit (entire prompt cached)
- 0.5 = Half the prompt was cached
- 0.0 = Full cache miss (processed from scratch)

**Business Impact:**
- High efficiency = Good prompt reuse, lower latency
- Low efficiency = Wasted compute, could optimize prompts

---

## Recommended Implementation Plan

### Option 1: Enhance Existing `/slots` Endpoint (Recommended)

**Advantages:**
- Minimal API changes
- Backward compatible (adds fields, doesn't remove)
- Natural fit with existing slot monitoring

**Implementation:**

1. **Modify `server_slot::to_json()` (server-context.cpp:414-450)**

Add KV cache section:
```cpp
json to_json(bool only_metrics = false) const {
    json res;

    res = {
        {"id",            id},
        {"n_ctx",         n_ctx},
        {"speculative",   can_speculate()},
        {"state",         get_state_str()},
        {"prompt_n",      n_prompt_tokens_processed},
        {"prompt_ms",     t_prompt_processing},
        {"predicted_n",   n_decoded},
        {"predicted_ms",  t_token_generation},
        {"cache_n",       n_prompt_tokens_cache},
    };

    // NEW: Add per-slot KV cache metrics
    llama_memory_t mem = llama_get_memory(ctx);
    if (mem) {
        llama_pos pos_min = llama_memory_seq_pos_min(mem, id);
        llama_pos pos_max = llama_memory_seq_pos_max(mem, id);

        res["kv_cache"] = {
            {"pos_min", pos_min},
            {"pos_max", pos_max},
            {"cells_used", (pos_min >= 0 && pos_max >= 0) ? (pos_max - pos_min + 1) : 0},
            {"utilization", n_ctx > 0 ? (float)pos_max / n_ctx : 0.0f},
            {"cache_efficiency", n_prompt_tokens_processed > 0
                ? (float)n_prompt_tokens_cache / n_prompt_tokens_processed
                : 0.0f}
        };
    }

    // NEW: Add performance metrics from get_timings()
    if (n_prompt_tokens_processed > 0 || n_decoded > 0) {
        res["performance"] = {
            {"prompt_tokens_per_sec", n_prompt_tokens_processed > 0
                ? 1e3 / t_prompt_processing * n_prompt_tokens_processed
                : 0.0},
            {"generation_tokens_per_sec", n_decoded > 0
                ? 1e3 / t_token_generation * n_decoded
                : 0.0}
        };

        // Add speculative decoding stats if applicable
        if (n_draft_total > 0) {
            res["performance"]["speculative_acceptance_rate"] =
                (float)n_draft_accepted / n_draft_total;
            res["performance"]["draft_tokens_total"] = n_draft_total;
            res["performance"]["draft_tokens_accepted"] = n_draft_accepted;
        }
    }

    // Existing next_token section...
    const auto & ptask = task ? task : task_prev;
    // ... rest of method
}
```

2. **Expected Output:**

```json
{
  "id": 0,
  "n_ctx": 65536,
  "speculative": true,
  "state": "generating",
  "prompt_n": 1024,
  "prompt_ms": 245.3,
  "predicted_n": 128,
  "predicted_ms": 1024.5,
  "cache_n": 400,
  "kv_cache": {
    "pos_min": 0,
    "pos_max": 1152,
    "cells_used": 1152,
    "utilization": 0.0176,
    "cache_efficiency": 0.391
  },
  "performance": {
    "prompt_tokens_per_sec": 4175.0,
    "generation_tokens_per_sec": 125.0,
    "speculative_acceptance_rate": 0.42,
    "draft_tokens_total": 256,
    "draft_tokens_accepted": 107
  }
}
```

**Lines to Modify:**
- `server-context.cpp:414-450` - Update `to_json()` method

---

### Option 2: New Dedicated Endpoint `/slots/{id}/kv-cache`

**Advantages:**
- Very detailed inspection without cluttering main endpoint
- Can be rate-limited separately
- Allows deep-dive debugging when needed

**Implementation:**

Create new endpoint in server routes:
```cpp
// In server-routes.cpp or equivalent
svr->Get("/slots/:id/kv-cache", [&ctx_server](const httplib::Request & req, httplib::Response & res) {
    int slot_id = std::stoi(req.path_params.at("id"));

    // Fetch slot from server context
    auto slot = ctx_server.get_slot(slot_id);
    if (!slot) {
        res.status = 404;
        res.set_content("{\"error\": \"Slot not found\"}", "application/json");
        return;
    }

    // Build detailed KV cache report
    json cache_info = get_detailed_kv_cache_info(slot);
    res.set_content(cache_info.dump(), "application/json");
});
```

**Response Format:**
```json
{
  "slot_id": 0,
  "sequence_id": 0,
  "global_stats": {
    "total_capacity": 200192,
    "total_used": 2048,
    "usage_ratio": 0.010
  },
  "slot_allocation": {
    "pos_min": 0,
    "pos_max": 1152,
    "cells_allocated": 1152,
    "context_window_size": 65536,
    "context_utilization": 0.0176
  },
  "cache_reuse": {
    "prompt_tokens_total": 1024,
    "prompt_tokens_cached": 400,
    "cache_hit_ratio": 0.391,
    "estimated_time_saved_ms": 148.9
  },
  "performance": {
    "prompt_tokens_per_sec": 4175.0,
    "generation_tokens_per_sec": 125.0,
    "time_since_last_use_ms": 1234
  },
  "memory_type": "llama_kv_cache_iswa",
  "shift_history": {
    "shifts_performed": 0,
    "tokens_discarded": 0
  }
}
```

---

## Integration with temper-view Dashboard

### Current Dashboard Capabilities

Based on CLAUDE.md, temper-view already has:
- Real-time GPU metrics visualization (Recharts)
- TanStack React Query for data fetching
- Dedicated chart components (PowerChart, TempChart, MemoryChart)

### Proposed New Components

#### 1. **KVCacheUtilizationChart.tsx**
```typescript
interface KVCacheMetrics {
  timestamp: number;
  global_usage_ratio: number;
  total_capacity: number;
  total_used: number;
}

export function KVCacheUtilizationChart() {
  const { data } = useQuery({
    queryKey: ['kv-cache-metrics'],
    queryFn: async () => {
      const response = await fetch('/api/metrics');
      return response.json();
    },
    refetchInterval: 1000, // 1 second
  });

  return (
    <LineChart data={data}>
      <Line dataKey="kv_cache_usage_ratio" stroke="#8884d8" />
      <YAxis domain={[0, 1]} label="Cache Utilization" />
    </LineChart>
  );
}
```

#### 2. **SlotKVCacheTable.tsx**
```typescript
interface SlotKVCache {
  id: number;
  state: string;
  pos_min: number;
  pos_max: number;
  cells_used: number;
  utilization: number;
  cache_efficiency: number;
  prompt_tokens_per_sec: number;
}

export function SlotKVCacheTable() {
  const { data: slots } = useQuery({
    queryKey: ['slots'],
    queryFn: async () => {
      const response = await fetch('/api/slots'); // Via llama-proxy
      return response.json();
    },
    refetchInterval: 500,
  });

  return (
    <table>
      <thead>
        <tr>
          <th>Slot</th>
          <th>State</th>
          <th>Position</th>
          <th>Cells Used</th>
          <th>Utilization</th>
          <th>Cache Efficiency</th>
          <th>Speed (tok/s)</th>
        </tr>
      </thead>
      <tbody>
        {slots?.map(slot => (
          <tr key={slot.id}>
            <td>{slot.id}</td>
            <td>{slot.state}</td>
            <td>{slot.kv_cache.pos_min}-{slot.kv_cache.pos_max}</td>
            <td>{slot.kv_cache.cells_used}</td>
            <td>{(slot.kv_cache.utilization * 100).toFixed(1)}%</td>
            <td>{(slot.kv_cache.cache_efficiency * 100).toFixed(1)}%</td>
            <td>{slot.performance.prompt_tokens_per_sec.toFixed(0)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
```

### API Proxy Configuration

llama-proxy needs to forward `/slots` requests to llama-server:

```python
# In llama-proxy (Python HTTP proxy)
@app.route('/slots', methods=['GET'])
def get_slots():
    # Validate API key (existing auth logic)
    api_key = request.headers.get('Authorization', '').replace('Bearer ', '')
    if not validate_api_key(api_key):
        return {'error': 'Unauthorized'}, 401

    # Forward to llama-server
    response = requests.get(
        f'{LLAMA_SERVER_URL}/slots',
        headers={'Authorization': f'Bearer {LLAMA_API_KEY}'}
    )
    return response.json(), response.status_code
```

---

## Impact Assessment

### Performance Impact
**Negligible:**
- `llama_memory_seq_pos_min/max` are O(1) lookups
- All data already computed (no additional processing)
- JSON serialization adds ~100 bytes per slot

### Backward Compatibility
**Fully Compatible:**
- Only adds new fields to existing JSON responses
- Old clients ignore unknown fields (JSON spec)
- No breaking changes to API contracts

### Development Effort
**Small (Option 1):**
- Single file modification (server-context.cpp)
- ~50 lines of code
- No new endpoints or routes
- Reuses existing API functions

**Medium (Option 2):**
- New endpoint handler
- New route registration
- Additional testing surface
- More complex access control

---

## Testing Recommendations

### Unit Tests
```cpp
// Test per-slot KV cache metrics
TEST(ServerSlot, KVCacheMetricsInJSON) {
    server_slot slot;
    slot.id = 0;
    slot.n_ctx = 65536;
    slot.n_prompt_tokens_cache = 400;
    slot.n_prompt_tokens_processed = 1024;

    // Mock llama_memory_seq_pos_min/max to return test values
    mock_seq_pos_min_returns(0);
    mock_seq_pos_max_returns(1152);

    json j = slot.to_json();

    ASSERT_EQ(j["kv_cache"]["pos_min"], 0);
    ASSERT_EQ(j["kv_cache"]["pos_max"], 1152);
    ASSERT_EQ(j["kv_cache"]["cells_used"], 1152);
    ASSERT_NEAR(j["kv_cache"]["cache_efficiency"], 0.391, 0.01);
}
```

### Integration Tests
```bash
# Start llama-server with test model
./llama-server --model test-model.gguf --ctx-size 4096 --port 8082

# Send test request
curl -X POST http://localhost:8082/v1/chat/completions \
  -H "Authorization: Bearer test-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "test",
    "messages": [{"role": "user", "content": "Hello"}]
  }'

# Check slot metrics
curl http://localhost:8082/slots | jq '.[0].kv_cache'

# Expected output:
# {
#   "pos_min": 0,
#   "pos_max": 15,
#   "cells_used": 15,
#   "utilization": 0.0037,
#   "cache_efficiency": 0.0
# }
```

### Load Tests
```bash
# Verify metrics under concurrent load
for i in {1..10}; do
  curl -X POST http://localhost:8082/v1/chat/completions \
    -H "Authorization: Bearer test-key" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"test\",\"messages\":[{\"role\":\"user\",\"content\":\"Request $i\"}]}" &
done

# Monitor slot distribution
watch -n 1 'curl -s http://localhost:8082/slots | jq ".[].kv_cache.cells_used"'
```

---

## Questions for Clarification

Before implementing, please clarify:

### 1. Implementation Scope
- **Option 1** (enhance `/slots`), **Option 2** (new endpoint), or **both**?
- Should metrics be opt-in (query parameter) or always included?

### 2. Priority Metrics
Which metrics are most valuable for your use case?
- [ ] KV cache position/fragmentation
- [ ] Tokens/second efficiency
- [ ] Speculative decoding stats
- [ ] Cache reusability/efficiency
- [ ] Time-based metrics (idle time, session duration)

### 3. Integration Target
- Will this feed into temper-view dashboard?
- External monitoring system (Prometheus/Grafana)?
- Internal debugging/optimization?

### 4. Frequency Requirements
- Real-time updates (100ms-1s intervals)?
- Periodic snapshots (10s-60s intervals)?
- On-demand queries only?

---

## Final Assessment

### Overall Grade: ⭐⭐⭐⭐⭐ (Excellent Foundation)

**What You Did Well:**
1. ✅ **Proper Abstraction** - Used public API functions, not internal hacks
2. ✅ **Comprehensive Support** - Handled all memory architectures (standard, iSWA, hybrid)
3. ✅ **Clean Integration** - Followed existing patterns in metrics collection
4. ✅ **Error Handling** - Graceful degradation for non-KV contexts
5. ✅ **Documentation** - Clear commit messages and code comments

**What's Next:**
The foundation is solid. The natural evolution is exposing **per-slot granularity** to enable:
- Identifying slow requests
- Detecting cache inefficiencies
- Optimizing prompt reuse
- Monitoring context window utilization

Your commits successfully moved llama.cpp from "no KV cache visibility" to "global KV cache monitoring." The next step is "per-slot KV cache observability" for production-grade monitoring.

---

## Appendix: Code Reference Map

| Feature | File | Line Range | Function |
|---------|------|------------|----------|
| Public API declaration | include/llama.h | 534-540, 757-766 | API function headers |
| KV cache used cells | src/llama-context.cpp | 3119-3165 | `llama_get_kv_cache_used_cells()` |
| KV cache token count | src/llama-context.cpp | 3167-3215 | `llama_get_kv_cache_token_count()` |
| Core get_used_cells() | src/llama-kv-cache.cpp | 990-995 | `llama_kv_cache::get_used_cells()` |
| Slot structure | tools/server/server-context.cpp | 48-166 | `struct server_slot` |
| Slot JSON output | tools/server/server-context.cpp | 414-450 | `server_slot::to_json()` |
| Timing calculation | tools/server/server-context.cpp | 323-344 | `server_slot::get_timings()` |
| Metrics collection | tools/server/server-context.cpp | 1783-1785 | Metrics task handler |
| Metrics endpoint | tools/server/server-context.cpp | 3284-3291 | `/metrics` route handler |
| Metrics structure | tools/server/server-task.h | 494-525 | `server_task_result_metrics` |

---

## Document Metadata

- **Generated:** 2026-01-28
- **Model:** Claude Sonnet 4.5
- **Commits Reviewed:** 9e6eafdc8, b048c4e95
- **Codebase:** llama.cpp (commit 264026ab5)
- **LOC Analyzed:** ~250 lines across 6 files
