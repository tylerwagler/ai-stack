# API Reference - ai-stack

**Last Updated:** 2026-02-02
**System Version:** v1.0

This document provides complete reference for all API endpoints in the ai-stack system, including llama-server, temper, ai-proxy, and nginx-proxied endpoints.

## Table of Contents

1. [Authentication Overview](#authentication-overview)
2. [llama-server Endpoints](#llama-server-endpoints)
3. [temper Endpoints](#temper-endpoints)
4. [ai-proxy Endpoints](#ai-proxy-endpoints)
5. [nginx/temper-view Endpoints](#nginxtemper-view-endpoints)
6. [Error Responses](#error-responses)
7. [Data Types](#data-types)
8. [Rate Limiting](#rate-limiting)

## Authentication Overview

The ai-stack uses three distinct API keys for different purposes:

| API Key | Purpose | Used By | Header Format |
|---------|---------|---------|---------------|
| `LLAMA_API_KEY` | Internal llama-server auth | temper, healthchecks | `Authorization: Bearer {key}` |
| `METRICS_API_KEY` | Protect temper metrics | nginx, direct clients | `X-API-Key: {key}` |
| User API keys | User authentication | End users via ai-proxy | `Authorization: Bearer {key}` |

### Getting API Keys

- **LLAMA_API_KEY**: Set in `.env` file, generated during setup
- **METRICS_API_KEY**: Set in `.env` file, generated during setup
- **User API keys**: Created via temper-view dashboard after user registration

## llama-server Endpoints

**Base URL:** `http://localhost:8082`
**Path Prefix:** `/chat` (configured via `LLAMA_API_PREFIX` env var)
**Authentication:** All endpoints require `Authorization: Bearer {LLAMA_API_KEY}`

### GET /chat/health

Check server health and model loading status.

**Authentication:** Required

**Response (Healthy):**
```json
{
  "status": "ok"
}
```

**Response (Loading):**
```json
{
  "status": "loading",
  "load_progress": 0.573
}
```

**Fields:**
- `status` (string): Server status - `"ok"`, `"loading"`, or `"error"`
- `load_progress` (number, optional): Model loading progress, 0.0 to 1.0, only present during loading

**Status Codes:**
- `200 OK`: Health check successful
- `401 Unauthorized`: Invalid or missing LLAMA_API_KEY
- `503 Service Unavailable`: Server starting up

**Example:**
```bash
curl -H "Authorization: Bearer $LLAMA_API_KEY" \
  http://localhost:8082/chat/health
```

---

### GET /chat/v1/models

List loaded models and their status.

**Authentication:** Required

**Response:**
```json
{
  "models": [
    {
      "name": "model-name.gguf",
      "model": "model-name.gguf",
      "modified_at": "",
      "size": "",
      "digest": "",
      "type": "model",
      "description": "",
      "tags": [""],
      "capabilities": ["completion"],
      "parameters": "",
      "details": {
        "parent_model": "",
        "format": "gguf",
        "family": "",
        "families": [""],
        "parameter_size": "",
        "quantization_level": ""
      }
    }
  ],
  "object": "list",
  "data": [
    {
      "id": "model-name.gguf",
      "object": "model",
      "created": 1738502594,
      "owned_by": "llamacpp",
      "meta": {
        "vocab_type": 2,
        "n_vocab": 151936,
        "n_ctx_train": 262144,
        "n_embd": 2048,
        "n_params": 30532122624,
        "size": 17659361280
      }
    }
  ]
}
```

**Router Mode (models-max > 1):**

When llama-server is in router mode, the response includes status fields for each model:

```json
{
  "models": [
    {
      "id": "model-a.gguf",
      "status": {
        "value": "loaded",
        "load_progress": 1.0
      }
    },
    {
      "id": "model-b.gguf",
      "status": {
        "value": "loading",
        "load_progress": 0.42
      }
    }
  ]
}
```

**Status Codes:**
- `200 OK`: Successfully retrieved model list
- `401 Unauthorized`: Invalid or missing LLAMA_API_KEY
- `503 Service Unavailable`: Model loading in progress (non-router mode)

**Example:**
```bash
curl -H "Authorization: Bearer $LLAMA_API_KEY" \
  http://localhost:8082/chat/v1/models | jq .
```

---

### GET /chat/slots

Get status of all inference slots.

**Authentication:** Required

**Query Parameters:**
- `model` (optional): Filter by model name (router mode)

**Response:**
```json
[
  {
    "id": 0,
    "n_ctx": 131072,
    "speculative": false,
    "state": "idle",
    "prompt_n": 0,
    "prompt_ms": 0.0,
    "predicted_n": 0,
    "predicted_ms": 0.0,
    "cache_n": 0,
    "kv_cache": {
      "pos_min": -1,
      "pos_max": -1,
      "cells_used": 0,
      "utilization": 0.0,
      "cache_efficiency": 0.0
    }
  },
  {
    "id": 1,
    "n_ctx": 131072,
    "state": "processing",
    "prompt_n": 256,
    "prompt_ms": 120.5,
    "predicted_n": 42,
    "predicted_ms": 850.3,
    "kv_cache": {
      "pos_min": 0,
      "pos_max": 298,
      "cells_used": 298,
      "utilization": 0.0023,
      "cache_efficiency": 1.0
    }
  }
]
```

**Fields:**
- `id` (number): Slot identifier (0-based index)
- `n_ctx` (number): Context size for this slot
- `speculative` (boolean): Whether speculative decoding is enabled
- `state` (string): Slot state - `"idle"`, `"processing"`, or `"error"`
- `prompt_n` (number): Number of prompt tokens processed
- `prompt_ms` (number): Time spent processing prompt (milliseconds)
- `predicted_n` (number): Number of tokens generated
- `predicted_ms` (number): Time spent generating tokens (milliseconds)
- `cache_n` (number): Number of cached tokens
- `kv_cache` (object): KV cache statistics
  - `pos_min` (number): Minimum position in cache
  - `pos_max` (number): Maximum position in cache
  - `cells_used` (number): Number of cache cells used
  - `utilization` (number): Cache utilization ratio (0.0-1.0)
  - `cache_efficiency` (number): Cache hit efficiency (0.0-1.0)

**Status Codes:**
- `200 OK`: Successfully retrieved slot status
- `401 Unauthorized`: Invalid or missing LLAMA_API_KEY

**Example:**
```bash
curl -H "Authorization: Bearer $LLAMA_API_KEY" \
  "http://localhost:8082/chat/slots?model=model-name.gguf" | jq .
```

---

### GET /chat/metrics

Prometheus-formatted metrics for llama-server.

**Authentication:** Required

**Response Format:** Prometheus text format

**Example Response:**
```
# HELP llamacpp:prompt_tokens_total Number of prompt tokens processed.
# TYPE llamacpp:prompt_tokens_total counter
llamacpp:prompt_tokens_total 15234

# HELP llamacpp:tokens_predicted_total Number of generation tokens processed.
# TYPE llamacpp:tokens_predicted_total counter
llamacpp:tokens_predicted_total 8492

# HELP llamacpp:prompt_seconds_total Prompt process time
# TYPE llamacpp:prompt_seconds_total counter
llamacpp:prompt_seconds_total 42.5

# HELP llamacpp:tokens_predicted_seconds_total Predict process time
# TYPE llamacpp:tokens_predicted_seconds_total counter
llamacpp:tokens_predicted_seconds_total 325.8

# HELP llamacpp:n_decode_total Total number of llama_decode() calls
# TYPE llamacpp:n_decode_total counter
llamacpp:n_decode_total 8492

# HELP llamacpp:n_tokens_max Largest observed n_tokens.
# TYPE llamacpp:n_tokens_max counter
llamacpp:n_tokens_max 512

# HELP llamacpp:n_busy_slots_per_decode Average number of busy slots per llama_decode() call
# TYPE llamacpp:n_busy_slots_per_decode counter
llamacpp:n_busy_slots_per_decode 1.2

# HELP llamacpp:prompt_tokens_seconds Average prompt throughput in tokens/s.
# TYPE llamacpp:prompt_tokens_seconds gauge
llamacpp:prompt_tokens_seconds 358.5

# HELP llamacpp:predicted_tokens_seconds Average generation throughput in tokens/s.
# TYPE llamacpp:predicted_tokens_seconds gauge
llamacpp:predicted_tokens_seconds 26.1

# HELP llamacpp:requests_processing Number of requests processing.
# TYPE llamacpp:requests_processing gauge
llamacpp:requests_processing 2

# HELP llamacpp:requests_deferred Number of requests deferred.
# TYPE llamacpp:requests_deferred gauge
llamacpp:requests_deferred 0

# HELP llamacpp:kv_cache_usage_ratio KV cache usage ratio.
# TYPE llamacpp:kv_cache_usage_ratio gauge
llamacpp:kv_cache_usage_ratio 0.15

# HELP llamacpp:kv_cache_tokens Number of tokens in KV cache.
# TYPE llamacpp:kv_cache_tokens gauge
llamacpp:kv_cache_tokens 19660
```

**Metrics Types:**
- **Counters:** Monotonically increasing values (resets on server restart)
- **Gauges:** Point-in-time values that can increase or decrease

**Status Codes:**
- `200 OK`: Successfully retrieved metrics
- `401 Unauthorized`: Invalid or missing LLAMA_API_KEY

**Example:**
```bash
curl -H "Authorization: Bearer $LLAMA_API_KEY" \
  http://localhost:8082/chat/metrics
```

---

### GET /chat/props

Get model properties and server configuration.

**Authentication:** Required

**Response:**
```json
{
  "default_generation_settings": {
    "n_ctx": 131072,
    "n_predict": -1,
    "model": "model-name.gguf",
    "seed": 4294967295,
    "temperature": 0.8,
    "top_k": 40,
    "top_p": 0.95,
    "min_p": 0.05,
    "n_keep": 0,
    "n_probs": 0,
    "tfs_z": 1.0,
    "typical_p": 1.0,
    "repeat_last_n": 64,
    "repeat_penalty": 1.0,
    "presence_penalty": 0.0,
    "frequency_penalty": 0.0,
    "mirostat": 0,
    "mirostat_tau": 5.0,
    "mirostat_eta": 0.1,
    "penalize_nl": false,
    "stop": [],
    "n_batch": 2048,
    "n_ubatch": 512,
    "n_threads": 8
  },
  "total_slots": 4,
  "model_type": "llama",
  "chat_template": "jinja",
  "server_start_time": 1738502594
}
```

**Status Codes:**
- `200 OK`: Successfully retrieved properties
- `401 Unauthorized`: Invalid or missing LLAMA_API_KEY

**Example:**
```bash
curl -H "Authorization: Bearer $LLAMA_API_KEY" \
  http://localhost:8082/chat/props | jq .
```

---

## temper Endpoints

**Base URL:** `http://localhost:3001` (internal only, bound to 127.0.0.1)
**Authentication:** All endpoints require `X-API-Key: {METRICS_API_KEY}`

### GET /metrics

Get comprehensive system metrics including GPU stats, host stats, and AI service status.

**Authentication:** Required

**Response:**
```json
{
  "timestamp": 1738502594,
  "gpus": [
    {
      "index": 0,
      "name": "NVIDIA RTX 6000 Ada",
      "pciInfo": "0000:01:00.0",
      "temperature": 45,
      "temperatureMax": 90,
      "fanSpeed": 30,
      "powerUsage": 145.5,
      "powerLimit": 230.0,
      "memoryUsed": 12288,
      "memoryTotal": 49152,
      "utilizationGPU": 25,
      "utilizationMemory": 15,
      "clockSM": 2520,
      "clockMemory": 9001,
      "performanceState": 2,
      "throttleReasons": "None"
    },
    {
      "index": 1,
      "name": "NVIDIA RTX 6000 Ada",
      "pciInfo": "0000:02:00.0",
      "temperature": 42,
      "temperatureMax": 90,
      "fanSpeed": 30,
      "powerUsage": 140.2,
      "powerLimit": 230.0,
      "memoryUsed": 12288,
      "memoryTotal": 49152,
      "utilizationGPU": 22,
      "utilizationMemory": 14,
      "clockSM": 2520,
      "clockMemory": 9001,
      "performanceState": 2,
      "throttleReasons": "None"
    }
  ],
  "host": {
    "cpuTemp": 55.0,
    "ramUsedMB": 16384,
    "ramTotalMB": 65536,
    "ramUsagePercent": 25.0,
    "uptime": 345600
  },
  "ai_service": {
    "status": "ready",
    "load_progress": 1.0,
    "model": "model-name.gguf",
    "model_path": "/models/model-name.gguf",
    "slots_used": 0,
    "slots_total": 4,
    "n_ctx": 131072,
    "prompt_tokens_total": 15234,
    "tokens_predicted_total": 8492,
    "prompt_seconds_total": 42.5,
    "tokens_predicted_seconds_total": 325.8,
    "n_decode_total": 8492,
    "n_busy_slots_per_decode": 1.2,
    "prompt_tokens_seconds": 358.5,
    "predicted_tokens_seconds": 26.1,
    "kv_cache_usage_ratio": 0.15,
    "kv_cache_tokens": 19660,
    "requests_processing": 0,
    "requests_deferred": 0,
    "n_tokens_max": 512,
    "slots": [
      {
        "id": 0,
        "n_ctx": 131072,
        "tokens_cached": 0,
        "state": "idle",
        "prompt_n": 0,
        "prompt_ms": 0.0,
        "predicted_n": 0,
        "predicted_ms": 0.0,
        "cache_n": 0,
        "kv_cache": {
          "pos_min": -1,
          "pos_max": -1,
          "cells_used": 0,
          "utilization": 0.0,
          "cache_efficiency": 0.0
        }
      }
    ]
  }
}
```

**AI Service Status Values:**
- `offline`: llama-server not responding
- `loading`: Model loading in progress
- `ready`: Model loaded, accepting requests
- `idle`: Server running but no model loaded
- `error`: Error state

**Status Codes:**
- `200 OK`: Successfully retrieved metrics
- `401 Unauthorized`: Invalid or missing METRICS_API_KEY

**Example:**
```bash
curl -H "X-API-Key: $METRICS_API_KEY" \
  http://localhost:3001/metrics | jq .
```

---

## ai-proxy Endpoints

**Base URL:** `http://localhost:8081`
**Authentication:** All endpoints require user API key in `Authorization: Bearer {user_api_key}`

### POST /v1/chat/completions

OpenAI-compatible chat completion endpoint.

**Authentication:** Required (user API key)

**Request:**
```json
{
  "model": "claude-3-5-sonnet-20241022",
  "messages": [
    {
      "role": "user",
      "content": "What is the capital of France?"
    }
  ],
  "temperature": 0.7,
  "max_tokens": 1024,
  "stream": false
}
```

**Response (non-streaming):**
```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1738502594,
  "model": "claude-3-5-sonnet-20241022",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "The capital of France is Paris."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 14,
    "completion_tokens": 8,
    "total_tokens": 22
  }
}
```

**Response (streaming):**

When `stream: true`, response is SSE format:

```
data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1738502594,"model":"claude-3-5-sonnet-20241022","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1738502594,"model":"claude-3-5-sonnet-20241022","choices":[{"index":0,"delta":{"content":"The"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1738502594,"model":"claude-3-5-sonnet-20241022","choices":[{"index":0,"delta":{"content":" capital"},"finish_reason":null}]}

...

data: [DONE]
```

**Model Name Translation:**

ai-proxy automatically translates model names:
- `claude-3-5-sonnet-20241022` → Configured model (glm/nemotron)
- `claude-3-5-haiku-20241022` → Configured model
- Other names → Passed through as-is

**Status Codes:**
- `200 OK`: Successful completion
- `400 Bad Request`: Invalid request format
- `401 Unauthorized`: Invalid or missing user API key
- `429 Too Many Requests`: Rate limit exceeded
- `503 Service Unavailable`: llama-server unavailable

**Example:**
```bash
curl -X POST http://localhost:8081/v1/chat/completions \
  -H "Authorization: Bearer sk-ant-YOUR_USER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-3-5-sonnet-20241022",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

---

### GET /v1/models

List available models.

**Authentication:** Required (user API key)

**Response:**
```json
{
  "object": "list",
  "data": [
    {
      "id": "claude-3-5-sonnet-20241022",
      "object": "model",
      "created": 1738502594,
      "owned_by": "anthropic"
    },
    {
      "id": "claude-3-5-haiku-20241022",
      "object": "model",
      "created": 1738502594,
      "owned_by": "anthropic"
    }
  ]
}
```

**Status Codes:**
- `200 OK`: Successfully retrieved model list
- `401 Unauthorized`: Invalid or missing user API key

**Example:**
```bash
curl -H "Authorization: Bearer sk-ant-YOUR_USER_KEY" \
  http://localhost:8081/v1/models | jq .
```

---

## nginx/temper-view Endpoints

**Base URL:** `http://localhost:3000` (public)
**Note:** nginx automatically injects authentication headers for proxied endpoints

### GET /api/metrics

Proxied temper metrics endpoint with automatic authentication.

**Authentication:** Not required (nginx injects X-API-Key header automatically)

**Response:** Same as `temper GET /metrics` (see above)

**Example:**
```bash
curl http://localhost:3000/api/metrics | jq .
```

---

### POST /api/admin/switch-model

Admin endpoint to switch llama-server model (router mode only).

**Authentication:** Requires user session with admin role

**Request:**
```json
{
  "model": "nemotron"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Model switch initiated"
}
```

**Status Codes:**
- `200 OK`: Switch initiated successfully
- `401 Unauthorized`: Not authenticated or not admin
- `400 Bad Request`: Invalid model name
- `503 Service Unavailable`: Switch failed

---

### Static Assets

All other paths serve the React SPA:

- `/` → index.html
- `/dashboard` → index.html (client-side routing)
- `/settings` → index.html (client-side routing)
- `/assets/*` → Static assets (JS, CSS, images)

---

## Error Responses

All endpoints use consistent error response format:

### 401 Unauthorized

```json
{
  "error": {
    "message": "Invalid API Key",
    "type": "authentication_error",
    "code": 401
  }
}
```

### 404 Not Found

```json
{
  "error": {
    "message": "File Not Found",
    "type": "not_found_error",
    "code": 404
  }
}
```

### 503 Service Unavailable

```json
{
  "error": {
    "message": "Model loading in progress",
    "type": "service_unavailable",
    "code": 503
  }
}
```

### 500 Internal Server Error

```json
{
  "error": {
    "message": "Internal server error",
    "type": "internal_error",
    "code": 500
  }
}
```

---

## Data Types

### LlamaStatus

String enum representing AI service status:

- `"offline"`: llama-server not responding or unreachable
- `"loading"`: Model loading in progress
- `"ready"`: Model fully loaded and accepting requests
- `"idle"`: Server running but no model loaded
- `"error"`: Error state (check logs for details)

### SlotState

String enum representing inference slot state:

- `"idle"`: Slot available for new request
- `"processing"`: Slot actively processing request
- `"error"`: Slot in error state

### KVCache

Object describing KV cache statistics for a slot:

```typescript
interface KVCache {
  pos_min: number;        // Minimum position (-1 if empty)
  pos_max: number;        // Maximum position (-1 if empty)
  cells_used: number;     // Number of cache cells used
  utilization: number;    // Utilization ratio (0.0-1.0)
  cache_efficiency: number; // Cache hit efficiency (0.0-1.0)
}
```

---

## Rate Limiting

### ai-proxy

- **API Key Validation:** Cached for 60 seconds per key
- **Request Rate:** Limited by slot availability (4 concurrent requests)
- **Token Rate:** No explicit limit, controlled by model throughput

### temper

- **Internal Polling:** 100ms interval (600 requests/minute to llama-server)
- **External Access:** No rate limit (nginx handles connection limiting)

### llama-server

- **Slot Saturation:** Maximum concurrent requests = number of slots
- **Queue:** Requests queue when all slots busy
- **No Hard Limit:** Controlled by slot availability and model throughput

---

## Debugging

### Enable Verbose Logging

**temper (fan-manager):**
```bash
docker exec fan-manager sh -c "export VERBOSE=1 && killall temper"
docker compose logs -f fan-manager
```

**llama-server:**
```bash
docker compose logs -f llama-server
```

**ai-proxy:**
```bash
docker compose logs -f ai-proxy
```

### Test Authentication

```bash
# Source environment variables
export $(cat .env | grep -v '^#' | xargs)

# Test llama-server
curl -v -H "Authorization: Bearer $LLAMA_API_KEY" http://localhost:8082/chat/health

# Test temper
curl -v -H "X-API-Key: $METRICS_API_KEY" http://localhost:3001/metrics

# Test nginx proxy
curl -v http://localhost:3000/api/metrics
```

### Monitor Metrics in Real-Time

```bash
# Watch GPU and AI metrics
watch -n 0.1 'curl -s http://localhost:3000/api/metrics | jq ".ai_service"'

# Monitor slot usage
watch -n 0.5 'curl -s -H "Authorization: Bearer $LLAMA_API_KEY" http://localhost:8082/chat/slots | jq ".[].state"'
```

---

## See Also

- [AUTHENTICATION.md](./AUTHENTICATION.md) - Detailed authentication guide
- [STATUS-STATES.md](./STATUS-STATES.md) - AI service status state machine
- [TESTING-PLAYBOOK.md](./TESTING-PLAYBOOK.md) - Testing procedures
- [../CLAUDE.md](../CLAUDE.md) - Development guide
