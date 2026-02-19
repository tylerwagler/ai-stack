"""
service_poller.py — Background poller for all LLM backend health/status.

Discovers unique backend hosts from ModelRegistry and polls them every ~3s.
Supports llama.cpp and vLLM backends with graceful offline fallback.
"""

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Dict, List, Any

from model_registry import ModelRegistry

logger = logging.getLogger(__name__)

POLL_INTERVAL = 3  # seconds


def _empty_status():
    """Return a blank AiServiceMetrics-compatible dict."""
    return {
        "status": "offline",
        "load_progress": 0,
        "model": "",
        "model_path": "",
        "slots_used": 0,
        "slots_total": 0,
        "n_ctx": 0,
        "prompt_tokens_total": 0,
        "prompt_seconds_total": 0,
        "tokens_predicted_total": 0,
        "tokens_predicted_seconds_total": 0,
        "prompt_tokens_seconds": 0,
        "predicted_tokens_seconds": 0,
        "requests_processing": 0,
        "requests_deferred": 0,
        "n_decode_total": 0,
        "n_tokens_max": 0,
        "n_busy_slots_per_decode": 0,
        "kv_cache_usage_ratio": 0.0,
        "kv_cache_tokens": 0,
    }


def _parse_prometheus(text: str) -> Dict[str, float]:
    """Parse Prometheus exposition format into {metric_name: value}."""
    result = {}
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split()
        if len(parts) >= 2:
            key = parts[0].split("{")[0]
            try:
                result[key] = float(parts[1])
            except ValueError:
                pass
    return result


def _parse_prometheus_labels(text: str, metric_name: str) -> Dict[str, str]:
    """Extract label key=value pairs from a specific Prometheus metric line."""
    for line in text.splitlines():
        if line.startswith(metric_name + "{"):
            m = re.search(r'\{(.+)\}\s', line)
            if m:
                labels = {}
                # Parse key="value" pairs (handles commas inside values)
                for pair in re.findall(r'(\w+)="([^"]*)"', m.group(1)):
                    labels[pair[0]] = pair[1]
                return labels
    return {}


def _http_get(url: str, api_key: str = "", timeout: int = 3) -> bytes:
    """Simple HTTP GET returning response body bytes. Raises on error."""
    req = urllib.request.Request(url)
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


class BackendStatus:
    """Thread-safe per-host status storage."""

    def __init__(self, host: str, backend: str, is_local: bool):
        self.host = host
        self.backend = backend
        self.is_local = is_local
        self._data: Dict[str, Any] = _empty_status()
        self._lock = threading.Lock()
        # Delta tracking for computing token rates from cumulative counters
        self._prev_gen_tokens: float = 0.0
        self._prev_prompt_tokens: float = 0.0
        self._prev_time: float = 0.0

    def update(self, data: Dict[str, Any]):
        with self._lock:
            self._data = data

    def compute_rates(self, gen_tokens: float, prompt_tokens: float) -> tuple:
        """Compute per-second rates from cumulative counters. Returns (gen_t/s, prompt_t/s)."""
        now = time.time()
        gen_rate = 0.0
        prompt_rate = 0.0
        if self._prev_time > 0:
            elapsed = now - self._prev_time
            if elapsed > 0.5:  # Avoid division by tiny intervals
                gen_rate = max(0, (gen_tokens - self._prev_gen_tokens) / elapsed)
                prompt_rate = max(0, (prompt_tokens - self._prev_prompt_tokens) / elapsed)
        self._prev_gen_tokens = gen_tokens
        self._prev_prompt_tokens = prompt_tokens
        self._prev_time = now
        return gen_rate, prompt_rate

    def get(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "host": self.host,
                "backend": self.backend,
                "is_local": self.is_local,
                **dict(self._data),
            }


class ServicePoller:
    """Discovers unique backend hosts from ModelRegistry and polls them."""

    def __init__(self, registry: ModelRegistry, api_key: str = ""):
        self.registry = registry
        self.api_key = api_key
        self.backends: Dict[str, BackendStatus] = {}
        self._discover_backends()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        logger.info("ServicePoller started (%d backends)", len(self.backends))

    def get_all(self) -> List[Dict[str, Any]]:
        return [b.get() for b in self.backends.values()]

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def _discover_backends(self):
        """Build one BackendStatus per unique host from the model registry."""
        for model in self.registry.list_models():
            host = model.host
            if host in self.backends:
                continue
            # Determine backend type from the first model on this host
            backend_type = "llama" if model.backend in ("llama", "llama.cpp") else "vllm"
            self.backends[host] = BackendStatus(host, backend_type, model.is_local)
            logger.info("Discovered backend: %s (%s, local=%s)", host, backend_type, model.is_local)

    # ------------------------------------------------------------------
    # Poll loop
    # ------------------------------------------------------------------
    def _run(self):
        while True:
            for host, bs in self.backends.items():
                try:
                    if bs.backend == "llama":
                        data = self._poll_llama(host)
                    else:
                        data = self._poll_vllm(host, bs)
                    bs.update(data)
                except Exception as e:
                    logger.debug("Poll error for %s: %s", host, e)
                    bs.update(_empty_status())
            time.sleep(POLL_INTERVAL)

    # ------------------------------------------------------------------
    # llama.cpp polling
    # ------------------------------------------------------------------
    def _poll_llama(self, host: str) -> Dict[str, Any]:
        data = _empty_status()
        base = f"http://{host}"

        # 1. Health + slots
        try:
            raw = _http_get(f"{base}/health", self.api_key)
            health = json.loads(raw)
            status = health.get("status", "unknown")
            progress = health.get("progress", health.get("loading_progress", 0))

            if status == "ok":
                data["status"] = "ready"
                data["load_progress"] = 1.0
            elif "loading" in status.lower():
                data["status"] = "loading"
                data["load_progress"] = progress
            else:
                data["status"] = status

            slots = health.get("slots", [])
            data["slots_total"] = len(slots)
            data["slots_used"] = sum(1 for s in slots if s.get("is_processing"))
        except urllib.error.HTTPError as e:
            if e.code == 503:
                try:
                    body = json.loads(e.read())
                    data["status"] = "loading"
                    data["load_progress"] = body.get("progress", body.get("loading_progress", 0))
                except Exception:
                    data["status"] = "loading"
            else:
                return _empty_status()
        except Exception:
            return _empty_status()

        # 2. Model name from /props and slot data from /slots (only when ready)
        if data["status"] == "ready":
            try:
                raw = _http_get(f"{base}/props", self.api_key)
                props = json.loads(raw)
                data["model"] = props.get("model_alias", "") or props.get("default_generation_settings", {}).get("model", "")
                data["model_path"] = props.get("model_path", "")
            except Exception:
                pass

            # Per-slot KV cache and performance metrics
            try:
                raw = _http_get(f"{base}/slots", self.api_key)
                slots_list = json.loads(raw)
                data["slots_total"] = len(slots_list)
                data["slots_used"] = sum(1 for s in slots_list if s.get("is_processing") or s.get("state") == "processing")
                # Extract fields the frontend expects (SlotMetrics type)
                compact_slots = []
                for s in slots_list:
                    kv = s.get("kv_cache", {})
                    # tokens_cached: use pos_max from kv_cache (actual context consumed)
                    # pos_max is the highest token position; -1 means empty
                    pos_max = kv.get("pos_max", -1)
                    tokens_cached = (pos_max + 1) if pos_max >= 0 else 0
                    slot = {
                        "id": s.get("id", 0),
                        "n_ctx": s.get("n_ctx", 0),
                        "tokens_cached": tokens_cached,
                        "state": s.get("state", "idle"),
                        "prompt_n": s.get("prompt_n", 0),
                        "prompt_ms": s.get("prompt_ms", 0),
                        "predicted_n": s.get("predicted_n", 0),
                        "predicted_ms": s.get("predicted_ms", 0),
                        "cache_n": s.get("cache_n", 0),
                    }
                    if kv:
                        slot["kv_cache"] = kv
                    if "performance" in s:
                        slot["performance"] = s["performance"]
                    compact_slots.append(slot)
                data["slots"] = compact_slots
                # Set n_ctx from first slot
                if compact_slots:
                    data["n_ctx"] = compact_slots[0]["n_ctx"]
            except Exception:
                pass

        # 3. Prometheus metrics
        try:
            raw = _http_get(f"{base}/metrics", self.api_key)
            parsed = _parse_prometheus(raw.decode())
            data["prompt_tokens_total"] = parsed.get("llamacpp:prompt_tokens_total", 0)
            data["prompt_seconds_total"] = parsed.get("llamacpp:prompt_seconds_total", 0)
            data["tokens_predicted_total"] = parsed.get("llamacpp:tokens_predicted_total", 0)
            data["tokens_predicted_seconds_total"] = parsed.get("llamacpp:tokens_predicted_seconds_total", 0)
            data["prompt_tokens_seconds"] = parsed.get("llamacpp:prompt_tokens_seconds", 0)
            data["predicted_tokens_seconds"] = parsed.get("llamacpp:predicted_tokens_seconds", 0)
            data["kv_cache_usage_ratio"] = parsed.get("llamacpp:kv_cache_usage_ratio", 0)
            data["kv_cache_tokens"] = int(parsed.get("llamacpp:kv_cache_tokens", 0))
            data["requests_processing"] = int(parsed.get("llamacpp:requests_processing", 0))
            data["requests_deferred"] = int(parsed.get("llamacpp:requests_deferred", 0))
            data["n_decode_total"] = int(parsed.get("llamacpp:n_decode_total", 0))
        except Exception:
            pass

        return data

    # ------------------------------------------------------------------
    # vLLM polling
    # ------------------------------------------------------------------
    def _poll_vllm(self, host: str, bs: BackendStatus) -> Dict[str, Any]:
        data = _empty_status()
        base = f"http://{host}"

        # 1. Health check
        try:
            _http_get(f"{base}/health", timeout=3)
            data["status"] = "ready"
            data["load_progress"] = 1.0
        except Exception:
            return _empty_status()

        # 2. Model info from /v1/models
        try:
            raw = _http_get(f"{base}/v1/models", timeout=3)
            models_resp = json.loads(raw)
            models_list = models_resp.get("data", [])
            if models_list:
                data["model"] = models_list[0].get("id", "")
                data["n_ctx"] = models_list[0].get("max_model_len", 0)
        except Exception:
            pass

        # 3. Prometheus metrics
        try:
            raw = _http_get(f"{base}/metrics", timeout=3)
            text = raw.decode()
            parsed = _parse_prometheus(text)

            # Token counters
            gen_total = parsed.get("vllm:generation_tokens_total", 0)
            prompt_total = parsed.get("vllm:prompt_tokens_total", 0)
            data["prompt_tokens_total"] = prompt_total
            data["tokens_predicted_total"] = gen_total

            # Compute instantaneous T/s from counter deltas
            gen_rate, prompt_rate = bs.compute_rates(gen_total, prompt_total)
            data["predicted_tokens_seconds"] = round(gen_rate, 1)
            data["prompt_tokens_seconds"] = round(prompt_rate, 1)

            # Request state
            data["requests_processing"] = int(parsed.get("vllm:num_requests_running", 0))
            data["requests_deferred"] = int(parsed.get("vllm:num_requests_waiting", 0))
            data["n_decode_total"] = int(parsed.get("vllm:request_success_total", 0))
            data["num_preemptions"] = int(parsed.get("vllm:num_preemptions_total", 0))

            # KV cache
            usage_ratio = parsed.get("vllm:kv_cache_usage_perc", 0)
            data["kv_cache_usage_ratio"] = usage_ratio

            # Derive total KV pool capacity from cache_config_info labels
            cache_labels = _parse_prometheus_labels(text, "vllm:cache_config_info")
            num_gpu_blocks = int(cache_labels.get("num_gpu_blocks", 0))
            block_size = int(cache_labels.get("block_size", 0))
            kv_pool_tokens = num_gpu_blocks * block_size
            data["kv_cache_pool_tokens"] = kv_pool_tokens
            data["kv_cache_tokens"] = int(usage_ratio * kv_pool_tokens)

            # Prefix cache hit rate
            cache_queries = parsed.get("vllm:prefix_cache_queries_total", 0)
            cache_hits = parsed.get("vllm:prefix_cache_hits_total", 0)
            data["prefix_cache_hit_rate"] = (cache_hits / cache_queries) if cache_queries > 0 else 0.0
            data["prefix_cache_queries"] = int(cache_queries)
            data["prefix_cache_hits"] = int(cache_hits)

            # Latency (histogram sums / counts → averages)
            e2e_sum = parsed.get("vllm:e2e_request_latency_seconds_sum", 0)
            e2e_count = parsed.get("vllm:e2e_request_latency_seconds_count", 0)
            data["avg_e2e_latency_seconds"] = (e2e_sum / e2e_count) if e2e_count > 0 else 0.0

            ttft_sum = parsed.get("vllm:time_to_first_token_seconds_sum", 0)
            ttft_count = parsed.get("vllm:time_to_first_token_seconds_count", 0)
            data["avg_time_to_first_token_seconds"] = (ttft_sum / ttft_count) if ttft_count > 0 else 0.0

            itl_sum = parsed.get("vllm:inter_token_latency_seconds_sum", 0)
            itl_count = parsed.get("vllm:inter_token_latency_seconds_count", 0)
            data["avg_inter_token_latency_seconds"] = (itl_sum / itl_count) if itl_count > 0 else 0.0
        except Exception:
            pass

        return data
