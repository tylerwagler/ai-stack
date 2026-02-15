"""
host-metrics: Read-only GPU and host metrics service.
Exposes /metrics JSON API on port 3001.
"""

import http.server
import json
import os
import signal
import socketserver
import sys
import threading
import time
import urllib.request

import psutil
import pynvml

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PORT = int(os.environ.get("METRICS_PORT", "3001"))
METRICS_API_KEY = os.environ.get("METRICS_API_KEY", "")
POLL_INTERVAL = 0.1  # 100 ms

LLAMA_HOST = os.environ.get("LLAMA_HOST", "")
LLAMA_PORT = os.environ.get("LLAMA_PORT", "8082")
LLAMA_API_KEY = os.environ.get("LLAMA_API_KEY", "")
LLAMA_API_PREFIX = os.environ.get("LLAMA_API_PREFIX", "/chat")
VERBOSE = os.environ.get("VERBOSE", "0") == "1"

# ---------------------------------------------------------------------------
# P-State descriptions
# ---------------------------------------------------------------------------
P_STATE_DESC = {
    0: "Maximum Performance",
    1: "High Performance",
    2: "Balanced Performance",
    3: "Adaptive",
    5: "Low Power Idle",
    8: "Minimum Power",
    15: "Idle / Low Power",
}

# ---------------------------------------------------------------------------
# Throttle reason parsing
# ---------------------------------------------------------------------------
THROTTLE_REASONS = {
    0x0000000000000001: "GPU Idle",
    0x0000000000000002: "App Clocks Setting",
    0x0000000000000004: "SW Power Cap",
    0x0000000000000008: "HW Slowdown",
    0x0000000000000010: "Sync Boost",
    0x0000000000000020: "SW Thermal Slowdown",
    0x0000000000000040: "HW Thermal Slowdown",
    0x0000000000000080: "HW Power Brake Slowdown",
    0x0000000000000100: "Display Clock Setting",
}


def parse_throttle(bitmask):
    if bitmask == 0 or bitmask == 1:  # idle is normal
        return "None", 0
    reasons = []
    for bit, desc in THROTTLE_REASONS.items():
        if bit == 1:
            continue  # skip idle
        if bitmask & bit:
            reasons.append(desc)
    alert = ", ".join(reasons) if reasons else "None"
    return alert, bitmask


# ---------------------------------------------------------------------------
# GPU Metrics Collector
# ---------------------------------------------------------------------------
class GPUCollector:
    def __init__(self):
        pynvml.nvmlInit()
        self.device_count = pynvml.nvmlDeviceGetCount()
        self.handles = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(self.device_count)]
        print(f"NVML initialized: {self.device_count} GPU(s) detected", file=sys.stderr)

    def collect(self):
        gpus = []
        for i, handle in enumerate(self.handles):
            try:
                gpus.append(self._read_gpu(i, handle))
            except pynvml.NVMLError as e:
                print(f"Error reading GPU {i}: {e}", file=sys.stderr)
        return gpus

    def _read_gpu(self, index, handle):
        name = self._str(pynvml.nvmlDeviceGetName(handle))
        serial = self._safe(pynvml.nvmlDeviceGetSerial, handle, "N/A")
        vbios = self._safe(pynvml.nvmlDeviceGetVbiosVersion, handle, "N/A")

        temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)

        # P-state
        try:
            ps = pynvml.nvmlDeviceGetPerformanceState(handle)
        except Exception:
            ps = 0
        ps_desc = P_STATE_DESC.get(ps, f"P{ps}")

        # Utilization
        try:
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            gpu_util, mem_util = util.gpu, util.memory
        except Exception:
            gpu_util = mem_util = 0

        # Memory
        try:
            mi = pynvml.nvmlDeviceGetMemoryInfo(handle)
            mem_used = int(mi.used / (1024 * 1024))
            mem_total = int(mi.total / (1024 * 1024))
        except Exception:
            mem_used = mem_total = 0

        # Fan
        try:
            fan = pynvml.nvmlDeviceGetFanSpeed(handle)
        except Exception:
            fan = 0

        # Power
        try:
            power_usage = pynvml.nvmlDeviceGetPowerUsage(handle)  # mW
        except Exception:
            power_usage = 0
        try:
            power_limit = pynvml.nvmlDeviceGetEnforcedPowerLimit(handle)  # mW
        except Exception:
            power_limit = 0

        # Clocks
        clocks = self._read_clocks(handle)

        # PCIe
        pcie = self._read_pcie(handle)

        # NVLink
        nvlink = self._read_nvlink(handle)

        # ECC
        ecc = self._read_ecc(handle)

        # Processes
        procs = self._read_processes(handle)

        # Throttle
        try:
            reasons = pynvml.nvmlDeviceGetCurrentClocksEventReasons(handle)
        except Exception:
            reasons = 0
        throttle_alert, throttle_bitmask = parse_throttle(reasons)

        return {
            "index": index,
            "name": name,
            "serial": serial,
            "vbios": vbios,
            "p_state": {"id": ps, "description": ps_desc},
            "temperature": temp,
            "fan_speed_percent": fan,
            "target_fan_percent": fan,
            "power_usage_mw": power_usage,
            "power_limit_mw": power_limit,
            "resources": {
                "gpu_load_percent": gpu_util,
                "memory_load_percent": mem_util,
                "memory_used_mb": mem_used,
                "memory_total_mb": mem_total,
            },
            "clocks": clocks,
            "pcie": pcie,
            "nvlink": nvlink,
            "ecc": ecc,
            "processes": procs,
            "throttle_alert": throttle_alert,
            "throttle_reason_bitmask": throttle_bitmask,
        }

    def _read_clocks(self, handle):
        def clk(t):
            try:
                return pynvml.nvmlDeviceGetClockInfo(handle, t)
            except Exception:
                return 0

        def mclk(t):
            try:
                return pynvml.nvmlDeviceGetMaxClockInfo(handle, t)
            except Exception:
                return 0

        return {
            "graphics": clk(pynvml.NVML_CLOCK_GRAPHICS),
            "memory": clk(pynvml.NVML_CLOCK_MEM),
            "sm": clk(pynvml.NVML_CLOCK_SM),
            "video": clk(pynvml.NVML_CLOCK_VIDEO),
            "max_graphics": mclk(pynvml.NVML_CLOCK_GRAPHICS),
            "max_memory": mclk(pynvml.NVML_CLOCK_MEM),
            "max_sm": mclk(pynvml.NVML_CLOCK_SM),
            "max_video": mclk(pynvml.NVML_CLOCK_VIDEO),
        }

    def _read_pcie(self, handle):
        try:
            tx = pynvml.nvmlDeviceGetPcieThroughput(handle, pynvml.NVML_PCIE_UTIL_TX_BYTES)
            rx = pynvml.nvmlDeviceGetPcieThroughput(handle, pynvml.NVML_PCIE_UTIL_RX_BYTES)
            gen = pynvml.nvmlDeviceGetCurrPcieLinkGeneration(handle)
            width = pynvml.nvmlDeviceGetCurrPcieLinkWidth(handle)
        except Exception:
            tx = rx = gen = width = 0
        return {"tx_throughput_kbs": tx, "rx_throughput_kbs": rx, "gen": gen, "width": width}

    def _read_nvlink(self, handle):
        result = {
            "active": False,
            "throughput_supported": False,
            "link_count": 0,
            "version": 0,
            "speed_mbps": 0,
            "tx_throughput_kbs": 0,
            "rx_throughput_kbs": 0,
        }
        try:
            for link in range(6):  # max 6 NVLink connections
                try:
                    state = pynvml.nvmlDeviceGetNvLinkState(handle, link)
                    if state:
                        result["active"] = True
                        result["link_count"] += 1
                        try:
                            result["version"] = pynvml.nvmlDeviceGetNvLinkVersion(handle, link)
                        except Exception:
                            pass
                except pynvml.NVMLError:
                    break
        except Exception:
            pass
        return result

    def _read_ecc(self, handle):
        def ec(et, ct):
            try:
                return pynvml.nvmlDeviceGetTotalEccErrors(handle, et, ct)
            except Exception:
                return 0
        return {
            "volatile_single": ec(pynvml.NVML_SINGLE_BIT_ECC, pynvml.NVML_VOLATILE_ECC),
            "volatile_double": ec(pynvml.NVML_DOUBLE_BIT_ECC, pynvml.NVML_VOLATILE_ECC),
            "aggregate_single": ec(pynvml.NVML_SINGLE_BIT_ECC, pynvml.NVML_AGGREGATE_ECC),
            "aggregate_double": ec(pynvml.NVML_DOUBLE_BIT_ECC, pynvml.NVML_AGGREGATE_ECC),
        }

    def _read_processes(self, handle):
        procs = []
        seen = set()
        for fn in (pynvml.nvmlDeviceGetComputeRunningProcesses, pynvml.nvmlDeviceGetGraphicsRunningProcesses):
            try:
                for p in fn(handle):
                    if p.pid in seen:
                        continue
                    seen.add(p.pid)
                    try:
                        name = self._str(pynvml.nvmlSystemGetProcessName(p.pid))
                    except Exception:
                        name = "Unknown"
                    procs.append({"pid": p.pid, "name": name, "used_memory": int(p.usedGpuMemory / (1024 * 1024))})
            except Exception:
                pass
        return procs

    @staticmethod
    def _str(val):
        return val.decode("utf-8") if isinstance(val, bytes) else str(val)

    @staticmethod
    def _safe(fn, handle, default):
        try:
            v = fn(handle)
            return v.decode("utf-8") if isinstance(v, bytes) else str(v)
        except Exception:
            return default


# ---------------------------------------------------------------------------
# Host Metrics Collector
# ---------------------------------------------------------------------------
def collect_host_metrics():
    mem = psutil.virtual_memory()
    load = os.getloadavg()
    return {
        "hostname": os.uname().nodename,
        "cpu_load_percent": psutil.cpu_percent(interval=None),
        "memory_total_mb": int(mem.total / (1024 * 1024)),
        "memory_available_mb": int(mem.available / (1024 * 1024)),
        "load_avg_1m": round(load[0], 2),
        "load_avg_5m": round(load[1], 2),
        "uptime_seconds": int(time.time() - psutil.boot_time()),
    }


# ---------------------------------------------------------------------------
# AI Service Poller (polls llama-server / vLLM for status)
# ---------------------------------------------------------------------------
class AIServicePoller:
    def __init__(self):
        self.metrics = self._empty()
        self._lock = threading.Lock()

    def _empty(self):
        return {
            "status": "offline",
            "load_progress": 0,
            "modelName": "",
            "modelPath": "",
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

    def get(self):
        with self._lock:
            return dict(self.metrics)

    def poll(self):
        if not LLAMA_HOST:
            return
        base = f"http://{LLAMA_HOST}:{LLAMA_PORT}{LLAMA_API_PREFIX}"
        health_url = f"{base}/health"
        try:
            req = urllib.request.Request(health_url)
            if LLAMA_API_KEY:
                req.add_header("Authorization", f"Bearer {LLAMA_API_KEY}")
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read())
                status = data.get("status", "unknown")
                progress = 1.0 if status == "ok" else data.get("loading_progress", 0)
                with self._lock:
                    self.metrics["status"] = "ready" if status == "ok" else status
                    self.metrics["load_progress"] = progress
                    slots = data.get("slots", [])
                    self.metrics["slots_total"] = len(slots)
                    self.metrics["slots_used"] = sum(1 for s in slots if s.get("is_processing"))
        except Exception:
            with self._lock:
                self.metrics = self._empty()

        # Also fetch /metrics for token throughput
        metrics_url = f"{base}/metrics"
        try:
            req = urllib.request.Request(metrics_url)
            if LLAMA_API_KEY:
                req.add_header("Authorization", f"Bearer {LLAMA_API_KEY}")
            with urllib.request.urlopen(req, timeout=2) as resp:
                text = resp.read().decode()
                parsed = self._parse_prometheus(text)
                with self._lock:
                    self.metrics["prompt_tokens_total"] = parsed.get("llamacpp:prompt_tokens_total", 0)
                    self.metrics["prompt_seconds_total"] = parsed.get("llamacpp:prompt_seconds_total", 0)
                    self.metrics["tokens_predicted_total"] = parsed.get("llamacpp:tokens_predicted_total", 0)
                    self.metrics["tokens_predicted_seconds_total"] = parsed.get("llamacpp:tokens_predicted_seconds_total", 0)
                    self.metrics["prompt_tokens_seconds"] = parsed.get("llamacpp:prompt_tokens_seconds", 0)
                    self.metrics["predicted_tokens_seconds"] = parsed.get("llamacpp:predicted_tokens_seconds", 0)
                    self.metrics["kv_cache_usage_ratio"] = parsed.get("llamacpp:kv_cache_usage_ratio", 0)
                    self.metrics["kv_cache_tokens"] = int(parsed.get("llamacpp:kv_cache_tokens", 0))
                    self.metrics["requests_processing"] = int(parsed.get("llamacpp:requests_processing", 0))
                    self.metrics["requests_deferred"] = int(parsed.get("llamacpp:requests_deferred", 0))
                    self.metrics["n_decode_total"] = int(parsed.get("llamacpp:n_decode_total", 0))
        except Exception:
            pass

    @staticmethod
    def _parse_prometheus(text):
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


# ---------------------------------------------------------------------------
# Metrics Store (thread-safe latest snapshot)
# ---------------------------------------------------------------------------
class MetricsStore:
    def __init__(self):
        self._data = {"host": {}, "gpus": [], "ai_service": {}}
        self._lock = threading.Lock()

    def update(self, host, gpus, ai_service):
        with self._lock:
            self._data = {"host": host, "gpus": gpus, "ai_service": ai_service}

    def get(self):
        with self._lock:
            return self._data


store = MetricsStore()


# ---------------------------------------------------------------------------
# Polling Thread
# ---------------------------------------------------------------------------
def poll_loop(gpu_collector, ai_poller):
    ai_tick = 0
    while True:
        try:
            gpus = gpu_collector.collect()
            host = collect_host_metrics()

            # Poll AI service less frequently (every 5s)
            ai_tick += 1
            if ai_tick >= 50:  # 50 * 100ms = 5s
                ai_poller.poll()
                ai_tick = 0

            store.update(host, gpus, ai_poller.get())
        except Exception as e:
            print(f"Poll error: {e}", file=sys.stderr)
        time.sleep(POLL_INTERVAL)


# ---------------------------------------------------------------------------
# HTTP Server
# ---------------------------------------------------------------------------
class MetricsHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self._json_response(200, {"status": "ok"})
            return

        if self.path != "/metrics":
            self.send_error(404)
            return

        if not self._check_auth():
            self._json_response(401, {"error": "Unauthorized"})
            return

        self._json_response(200, store.get())

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def _check_auth(self):
        if not METRICS_API_KEY:
            return True
        key = self.headers.get("x-api-key", "")
        if not key:
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                key = auth[7:]
        return key == METRICS_API_KEY

    def _json_response(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "x-api-key, Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")

    def log_message(self, fmt, *args):
        if VERBOSE:
            super().log_message(fmt, *args)


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    gpu_collector = GPUCollector()
    ai_poller = AIServicePoller()

    # Start polling thread
    t = threading.Thread(target=poll_loop, args=(gpu_collector, ai_poller), daemon=True)
    t.start()

    # Graceful shutdown
    def shutdown(sig, frame):
        print("\nShutting down...", file=sys.stderr)
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    server = ThreadedHTTPServer(("0.0.0.0", PORT), MetricsHandler)
    print(f"host-metrics listening on port {PORT} ({gpu_collector.device_count} GPUs)", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
