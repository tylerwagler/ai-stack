
import requests
import logging
import time

logger = logging.getLogger(__name__)

class VLLMMetricsParser:
    def __init__(self, vllm_url="http://vllm-server:8000/metrics"):
        self.vllm_url = vllm_url
        self.last_token_sum = 0.0
        self.last_time = time.time()
        self.current_tps = 0.0

    def parse(self, text):
        metrics = {}
        for line in text.splitlines():
            if line.startswith("#") or not line.strip():
                continue
            
            parts = line.split()
            if len(parts) >= 2:
                key = parts[0]
                # Handle labels like vllm:num_requests_running{...}
                if "{" in key:
                    key = key.split("{")[0]
                
                try:
                    val = float(parts[1])
                    # For counters/histograms with multiple lines (buckets), we might overwrite.
                    # Usually the _sum or _count or specific gauges come last or are unique enough?
                    # For simple gauges like num_requests_running it's fine.
                    # For total_sum, we want that specific line.
                    metrics[key] = val
                except:
                    pass
        return metrics

    def get_metrics(self):
        try:
            resp = requests.get(self.vllm_url, timeout=2)
            if resp.status_code != 200:
                logger.warning(f"Failed to fetch vLLM metrics: {resp.status_code}")
                return None
            
            raw_metrics = self.parse(resp.text)
            
            # Key metrics identified from dump
            gpu_cache_usage = raw_metrics.get("vllm:kv_cache_usage_perc", 0.0)
            num_running = raw_metrics.get("vllm:num_requests_running", 0.0)
            num_waiting = raw_metrics.get("vllm:num_requests_waiting", 0.0)
            
            # TPS Calculation
            token_sum = raw_metrics.get("vllm:iteration_tokens_total_sum", 0.0)
            now = time.time()
            
            if token_sum > 0 and self.last_token_sum > 0:
                delta_tokens = token_sum - self.last_token_sum
                delta_time = now - self.last_time
                if delta_time > 0:
                    self.current_tps = delta_tokens / delta_time
            
            # Update state
            self.last_token_sum = token_sum
            self.last_time = now
            
            result = {
                "status": "ready",
                "backend_type": "vllm",
                "predicted_tokens_seconds": self.current_tps,
                "kv_cache_usage_percent": gpu_cache_usage * 100, 
                "requests_running": int(num_running),
                "requests_waiting": int(num_waiting),
                
                # Mock Slot logic for frontend
                "slots": [
                    {
                        "id": 0,
                        "state": "processing" if num_running > 0 else "idle",
                        "n_ctx": 32768, 
                        "tokens_cached": int(32768 * gpu_cache_usage),
                        "performance": {
                            "generation_tokens_per_sec": self.current_tps
                        }
                    }
                ]
            }
            
            return result

        except Exception as e:
            logger.error(f"Error fetching vLLM metrics: {e}")
            return None
