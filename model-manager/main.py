"""
model-manager: Dynamic LLM container manager.

Loads/unloads inference containers (llama.cpp or vLLM) on demand via the
Docker Engine API. Exposes an HTTP API on port 8100 for catalog browsing,
loading, and unloading.  Uses only stdlib (zero pip dependencies).
"""

import configparser
import http.server
import json
import os
import re
import shutil
import signal
import socket
import socketserver
import ssl
import sys
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PORT = int(os.environ.get("MANAGER_PORT", "8100"))
LLAMA_IMAGE = os.environ.get("LLAMA_IMAGE", "llama-cpp-server:local")
VLLM_IMAGE = os.environ.get("VLLM_IMAGE", "vllm-node-tf5:latest")
MAX_GPU_UTILIZATION = float(os.environ.get("MAX_GPU_UTILIZATION", "0.95"))
MAX_CONCURRENT_MODELS = int(os.environ.get("MAX_CONCURRENT_MODELS", "0"))
DOCKER_SOCKET = "/var/run/docker.sock"
CONFIG_PATH = os.environ.get("CONFIG_PATH", "/app/models.ini")
HEALTH_POLL_INTERVAL = 5  # seconds between health checks for starting containers
STARTUP_TIMEOUT = 900  # 15 minutes max to wait for container to become healthy

# Volume mounts per backend
LLAMA_VOLUME_MOUNTS = os.environ.get(
    "LLAMA_VOLUMES",
    "/home/tyler/.cache/llama.cpp:/root/.cache/llama.cpp,"
    "/home/tyler/.cache/huggingface:/root/.cache/huggingface:ro",
).split(",")

VLLM_VOLUME_MOUNTS = os.environ.get(
    "VLLM_VOLUMES",
    "/home/tyler/.cache/huggingface:/root/.cache/huggingface,"
    "/home/tyler/.cache/vllm:/root/.cache/vllm,"
    "/home/tyler/.cache/flashinfer:/root/.cache/flashinfer,"
    "/home/tyler/.triton:/root/.triton",
).split(",")

# Host path to vLLM mods directory (contains per-model fix directories)
VLLM_MODS_DIR = os.environ.get("VLLM_MODS_DIR", "/home/tyler/ai-stack/spark-vllm-docker/mods")

# Labels for identifying managed containers
MANAGER_LABEL = "managed-by"
MANAGER_VALUE = "model-manager"
MODEL_LABEL = "model-id"


# ---------------------------------------------------------------------------
# Model Config (from INI)
# ---------------------------------------------------------------------------
class ModelConfig:
    """Unified model configuration supporting both llama.cpp and vLLM backends."""

    def __init__(self, section: str, config: configparser.SectionProxy):
        self.id = section
        self.display_name = config.get("display_name", section)
        self.model = config.get("model", "")
        self.backend = config.get("backend", "")  # "llama" or "vllm"
        self.port = int(config.get("port", "8000"))

        # Shared VRAM estimation
        self.vram_base = int(config.get("vram_base", "0"))
        self.vram_per_1k_ctx = int(config.get("vram_per_1k_ctx", "0"))

        # llama-specific
        self.ctx_size = int(config.get("ctx_size", "0"))
        self.tensor_split = config.get("tensor_split", "")
        self.parallel = int(config.get("parallel", "1"))
        self.cache_type = config.get("cache_type", "f16")

        # vllm-specific
        self.served_model_name = config.get("served_model_name", section)
        self.gpu_memory = float(config.get("gpu_memory", "0.45"))
        self.tp = int(config.get("tp", "1"))
        self.max_model_len = int(config.get("max_model_len", "131072"))
        self.max_num_seqs = int(config.get("max_num_seqs", "64"))
        self.max_num_batched_tokens = config.get("max_num_batched_tokens", "")
        self.vllm_args = config.get("vllm_args", "")
        self.vllm_env = config.get("vllm_env", "")  # space-separated KEY=VALUE pairs
        self.vllm_mods = config.get("vllm_mods", "")  # space-separated mod directory names
        self.kv_cache_dtype = config.get("kv_cache_dtype", "auto")

        # max_ctx: defaults to ctx_size (llama) or max_model_len (vllm)
        default_max = self.ctx_size if self.backend == "llama" else self.max_model_len
        self.max_ctx = int(config.get("max_ctx", str(default_max)))

        # Proxy routing fields (passed through to ai-proxy via catalog API)
        self.default_temperature = config.get("default_temperature", "")
        self.default_top_p = config.get("default_top_p", "")
        self.default_top_k = config.get("default_top_k", "")
        self.default_min_p = config.get("default_min_p", "")
        self.max_output = config.get("max_output", "")
        self.chat_visible = config.get("chat_visible", "true")

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "display_name": self.display_name,
            "model": self.model,
            "backend": self.backend,
            "port": self.port,
            "max_ctx": self.max_ctx,
            "vram_base": self.vram_base,
            "vram_per_1k_ctx": self.vram_per_1k_ctx,
        }
        if self.backend == "llama":
            d.update({
                "ctx_size": self.ctx_size,
                "tensor_split": self.tensor_split,
                "parallel": self.parallel,
                "cache_type": self.cache_type,
            })
        else:
            d.update({
                "served_model_name": self.served_model_name,
                "gpu_memory": self.gpu_memory,
                "tp": self.tp,
                "max_model_len": self.max_model_len,
                "max_num_seqs": self.max_num_seqs,
                "kv_cache_dtype": self.kv_cache_dtype,
            })
        # Proxy routing fields (only include when set)
        if self.default_temperature:
            d["default_temperature"] = self.default_temperature
        if self.default_top_p:
            d["default_top_p"] = self.default_top_p
        if self.default_top_k:
            d["default_top_k"] = self.default_top_k
        if self.default_min_p:
            d["default_min_p"] = self.default_min_p
        if self.max_output:
            d["max_output"] = self.max_output
        if self.chat_visible != "true":
            d["chat_visible"] = self.chat_visible
        return d


# ---------------------------------------------------------------------------
# Container State
# ---------------------------------------------------------------------------
class ContainerState:
    def __init__(self, model_id: str):
        self.model_id = model_id
        self.status = "stopped"  # stopped | starting | running | error
        self.container_id: Optional[str] = None
        self.started_at: Optional[float] = None
        self.error: Optional[str] = None
        self.progress: Optional[int] = None      # 0-100
        self.progress_msg: Optional[str] = None   # e.g. "Loading tensors 3/10"

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "model_id": self.model_id,
            "status": self.status,
            "container_id": self.container_id[:12] if self.container_id else None,
        }
        if self.started_at:
            d["uptime_seconds"] = int(time.time() - self.started_at)
        if self.error:
            d["error"] = self.error
        if self.progress is not None:
            d["progress"] = self.progress
        if self.progress_msg:
            d["progress_msg"] = self.progress_msg
        return d


# ---------------------------------------------------------------------------
# Docker Client (Unix socket, stdlib-only)
# ---------------------------------------------------------------------------
class DockerClient:
    """Minimal Docker Engine API client over Unix socket."""

    API_VERSION = "v1.45"

    def request(self, method: str, path: str, body: Optional[dict] = None,
                timeout: int = 30) -> tuple:
        """Send request to Docker daemon. Returns (status_code, response_body_dict)."""
        # Prefix with API version if not already present
        if not path.startswith(f"/{self.API_VERSION}/"):
            path = f"/{self.API_VERSION}{path}"
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(DOCKER_SOCKET)
            body_bytes = b""
            if body is not None:
                body_bytes = json.dumps(body).encode()

            headers = (
                f"{method} {path} HTTP/1.1\r\n"
                f"Host: localhost\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(body_bytes)}\r\n"
                f"Connection: close\r\n"
                f"\r\n"
            )
            sock.sendall(headers.encode() + body_bytes)

            # Read full response
            response = b""
            while True:
                try:
                    chunk = sock.recv(8192)
                    if not chunk:
                        break
                    response += chunk
                except socket.timeout:
                    break

            # Parse HTTP response
            header_end = response.find(b"\r\n\r\n")
            if header_end == -1:
                return 0, {}

            header_section = response[:header_end].decode(errors="replace")
            body_section = response[header_end + 4:]

            # Status code from first line
            status_line = header_section.split("\r\n")[0]
            status_code = int(status_line.split(" ", 2)[1])

            # Parse body as JSON if present
            resp_body = {}
            if body_section.strip():
                # Handle chunked transfer encoding
                if b"Transfer-Encoding: chunked" in response[:header_end]:
                    body_section = self._decode_chunked(body_section)
                try:
                    resp_body = json.loads(body_section)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    resp_body = {"raw": body_section.decode(errors="replace")[:500]}

            return status_code, resp_body
        finally:
            sock.close()

    @staticmethod
    def _decode_chunked(data: bytes) -> bytes:
        """Decode HTTP chunked transfer encoding."""
        result = b""
        while data:
            # Find chunk size line
            nl = data.find(b"\r\n")
            if nl == -1:
                break
            size_str = data[:nl].decode(errors="replace").strip()
            if not size_str:
                data = data[nl + 2:]
                continue
            try:
                chunk_size = int(size_str, 16)
            except ValueError:
                break
            if chunk_size == 0:
                break
            start = nl + 2
            result += data[start:start + chunk_size]
            data = data[start + chunk_size + 2:]  # skip trailing \r\n
        return result

    def list_containers(self, label_filter: str = "") -> list:
        """List containers, optionally filtered by label."""
        filters = {}
        if label_filter:
            filters["label"] = [label_filter]
        path = f"/containers/json?all=true&filters={urllib.parse.quote(json.dumps(filters))}" if filters else "/containers/json?all=true"
        status, body = self.request("GET", path)
        if status == 200 and isinstance(body, list):
            return body
        return []

    def inspect_container(self, container_id: str) -> dict:
        status, body = self.request("GET", f"/containers/{container_id}/json")
        return body if status == 200 else {}

    def create_container(self, name: str, config: dict) -> tuple:
        """Create a container. Returns (status_code, response_dict)."""
        return self.request("POST", f"/containers/create?name={name}", body=config)

    def start_container(self, container_id: str) -> int:
        status, _ = self.request("POST", f"/containers/{container_id}/start")
        return status

    def stop_container(self, container_id: str, timeout: int = 10) -> int:
        status, _ = self.request("POST", f"/containers/{container_id}/stop?t={timeout}", timeout=timeout + 5)
        return status

    def remove_container(self, container_id: str, force: bool = False) -> int:
        path = f"/containers/{container_id}?force=true" if force else f"/containers/{container_id}"
        status, _ = self.request("DELETE", path)
        return status

    def get_container_logs(self, container_id: str, tail: int = 200, timeout: int = 5) -> str:
        """Fetch container logs. Returns decoded log text."""
        path = f"/{self.API_VERSION}/containers/{container_id}/logs?stdout=true&stderr=true&tail={tail}&timestamps=true"
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(DOCKER_SOCKET)
            request = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: localhost\r\n"
                f"Connection: close\r\n"
                f"\r\n"
            )
            sock.sendall(request.encode())

            response = b""
            while True:
                try:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    response += chunk
                except socket.timeout:
                    break

            header_end = response.find(b"\r\n\r\n")
            if header_end == -1:
                return ""

            header_section = response[:header_end]
            body = response[header_end + 4:]

            # Handle chunked transfer encoding
            if b"Transfer-Encoding: chunked" in header_section:
                body = self._decode_chunked(body)

            # Docker logs use multiplexed stream format:
            # 8-byte header per frame: [stream_type(1), 0, 0, 0, size(4 big-endian)]
            # followed by `size` bytes of payload
            lines = []
            pos = 0
            while pos + 8 <= len(body):
                frame_size = int.from_bytes(body[pos + 4:pos + 8], "big")
                if frame_size == 0 or pos + 8 + frame_size > len(body):
                    break
                payload = body[pos + 8:pos + 8 + frame_size]
                lines.append(payload.decode(errors="replace").rstrip("\n"))
                pos += 8 + frame_size

            # If multiplexed parsing got nothing, treat as raw text (TTY mode)
            if not lines and body:
                return body.decode(errors="replace")

            return "\n".join(lines)
        finally:
            sock.close()

    def image_exists(self, image: str) -> bool:
        status, _ = self.request("GET", f"/images/{image}/json")
        return status == 200


# ---------------------------------------------------------------------------
# Update Checker
# ---------------------------------------------------------------------------
UPDATE_CHECK_TTL = 3600  # 1 hour cache
LLAMA_CACHE_DIR = os.environ.get("LLAMA_CACHE_DIR", "")
HF_CACHE_DIR = os.environ.get("HF_CACHE_DIR", "")


class UpdateChecker:
    """Checks HuggingFace for model updates by comparing local vs remote refs."""

    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}  # model_id -> result
        self._lock = threading.Lock()
        self._ssl_ctx = ssl.create_default_context()

    def check_all(self, catalog: Dict[str, "ModelConfig"]) -> List[Dict[str, Any]]:
        """Check all models for updates. Uses cache when valid."""
        results = []
        for model_id, cfg in catalog.items():
            results.append(self.check_model(model_id, cfg))
        return results

    def check_model(self, model_id: str, cfg: "ModelConfig") -> Dict[str, Any]:
        """Check a single model for updates. Returns cached result if fresh."""
        cached = self.get_cached(model_id)
        if cached is not None:
            return cached

        try:
            if cfg.backend == "llama":
                result = self._check_llama(model_id, cfg)
            else:
                result = self._check_vllm(model_id, cfg)
        except Exception as e:
            result = {
                "model_id": model_id,
                "backend": cfg.backend,
                "has_update": None,
                "error": str(e),
                "checked_at": time.time(),
            }

        with self._lock:
            self._cache[model_id] = result
        return result

    def get_cached(self, model_id: str) -> Optional[Dict[str, Any]]:
        """Return cached result if still within TTL."""
        with self._lock:
            result = self._cache.get(model_id)
        if result is None:
            return None
        if time.time() - result.get("checked_at", 0) > UPDATE_CHECK_TTL:
            return None
        return result

    def invalidate(self, model_id: Optional[str] = None):
        """Clear cache for one or all models."""
        with self._lock:
            if model_id:
                self._cache.pop(model_id, None)
            else:
                self._cache.clear()

    def _hf_api_get(self, path: str) -> Any:
        """GET from HuggingFace API. Returns parsed JSON."""
        url = f"https://huggingface.co/api/{path}"
        req = urllib.request.Request(url, headers={"User-Agent": "model-manager/1.0"})
        with urllib.request.urlopen(req, timeout=15, context=self._ssl_ctx) as resp:
            return json.loads(resp.read())

    def _check_llama(self, model_id: str, cfg: "ModelConfig") -> Dict[str, Any]:
        """Check llama.cpp model: compare local etag vs remote xetHash."""
        # Parse hf://org/repo/file.gguf
        if not cfg.model.startswith("hf://"):
            return self._no_check_result(model_id, cfg, "Not an HF model")
        parts = cfg.model[5:].split("/", 2)
        if len(parts) != 3:
            return self._no_check_result(model_id, cfg, "Invalid HF URL format")
        org, repo, filename = parts

        # Read local etag
        local_ref = None
        if LLAMA_CACHE_DIR:
            cache_name = f"{org}_{repo}_{filename}"
            etag_path = os.path.join(LLAMA_CACHE_DIR, f"{cache_name}.etag")
            try:
                with open(etag_path, "r") as f:
                    local_ref = f.read().strip().strip('"')
            except FileNotFoundError:
                pass

        # Get remote info
        repo_info = self._hf_api_get(f"models/{org}/{repo}")
        remote_commit = repo_info.get("sha", "")
        last_modified = repo_info.get("lastModified", "")

        # Get file tree to find xetHash
        tree = self._hf_api_get(f"models/{org}/{repo}/tree/main")
        remote_ref = None
        for entry in tree:
            if entry.get("path") == filename:
                remote_ref = entry.get("xetHash") or entry.get("lfs", {}).get("oid") or entry.get("oid")
                break

        has_update = None
        if local_ref and remote_ref:
            has_update = local_ref != remote_ref
        elif local_ref is None and remote_ref:
            has_update = None  # Can't determine — not downloaded yet

        return {
            "model_id": model_id,
            "backend": "llama",
            "has_update": has_update,
            "local_ref": local_ref[:16] + "..." if local_ref and len(local_ref) > 16 else local_ref,
            "remote_ref": remote_ref[:16] + "..." if remote_ref and len(remote_ref) > 16 else remote_ref,
            "remote_commit": remote_commit,
            "last_modified": last_modified,
            "checked_at": time.time(),
        }

    def _check_vllm(self, model_id: str, cfg: "ModelConfig") -> Dict[str, Any]:
        """Check vLLM model: compare local refs/main commit vs remote sha."""
        org_repo = cfg.model  # e.g. "unsloth/GLM-4.7-Flash-FP8-Dynamic"
        org, repo = org_repo.split("/", 1) if "/" in org_repo else (org_repo, "")
        if not repo:
            return self._no_check_result(model_id, cfg, "Invalid model name")

        # Read local commit from HF hub cache
        local_ref = None
        if HF_CACHE_DIR:
            refs_path = os.path.join(HF_CACHE_DIR, f"models--{org}--{repo}", "refs", "main")
            try:
                with open(refs_path, "r") as f:
                    local_ref = f.read().strip()
            except FileNotFoundError:
                pass

        # Get remote info
        repo_info = self._hf_api_get(f"models/{org}/{repo}")
        remote_commit = repo_info.get("sha", "")
        last_modified = repo_info.get("lastModified", "")

        has_update = None
        if local_ref and remote_commit:
            has_update = local_ref != remote_commit

        return {
            "model_id": model_id,
            "backend": "vllm",
            "has_update": has_update,
            "local_ref": local_ref[:16] + "..." if local_ref and len(local_ref) > 16 else local_ref,
            "remote_ref": remote_commit[:16] + "..." if remote_commit and len(remote_commit) > 16 else remote_commit,
            "remote_commit": remote_commit,
            "last_modified": last_modified,
            "checked_at": time.time(),
        }

    @staticmethod
    def _no_check_result(model_id: str, cfg: "ModelConfig", reason: str) -> Dict[str, Any]:
        return {
            "model_id": model_id,
            "backend": cfg.backend,
            "has_update": None,
            "error": reason,
            "checked_at": time.time(),
        }


# ---------------------------------------------------------------------------
# Model Manager
# ---------------------------------------------------------------------------
class ModelManager:
    def __init__(self):
        self.catalog: Dict[str, ModelConfig] = {}
        self.states: Dict[str, ContainerState] = {}
        self.docker = DockerClient()
        self.update_checker = UpdateChecker()
        self._lock = threading.Lock()
        self._health_threads: Dict[str, threading.Thread] = {}

        self._load_catalog()
        self._sync_containers()

    def _load_catalog(self):
        """Load model catalog from INI file."""
        parser = configparser.ConfigParser()
        parser.read(CONFIG_PATH)
        for section in parser.sections():
            cfg = ModelConfig(section, parser[section])
            if not cfg.backend:
                print(f"WARNING: Model [{section}] missing 'backend' field, skipping", file=sys.stderr)
                continue
            self.catalog[section] = cfg
            self.states[section] = ContainerState(section)
        print(f"Loaded {len(self.catalog)} models from catalog", file=sys.stderr)

    def _container_name(self, model_id: str, backend: str) -> str:
        safe_id = model_id.lower().replace(" ", "-")
        return f"{backend}-{safe_id}"

    def _sync_containers(self):
        """On startup, sync state with any existing managed containers."""
        containers = self.docker.list_containers(f"{MANAGER_LABEL}={MANAGER_VALUE}")
        for c in containers:
            labels = c.get("Labels", {})
            model_id = labels.get(MODEL_LABEL, "")
            if model_id not in self.states:
                continue

            state = self.states[model_id]
            container_state = c.get("State", "").lower()
            state.container_id = c.get("Id", "")

            if container_state == "running":
                created = c.get("Created", 0)
                state.started_at = created if created else time.time()
                # Check if actually ready (health endpoint responds 200)
                # If not, treat as still starting so health monitor tracks progress
                cfg = self.catalog.get(model_id)
                ready_path = "/props" if cfg and cfg.backend == "llama" else "/health"
                try:
                    resp = self._http_get("localhost", cfg.port, ready_path)
                    if resp is not None:
                        state.status = "running"
                    else:
                        state.status = "starting"
                except Exception:
                    state.status = "starting"
                self._start_health_monitor(model_id)
            elif container_state in ("created", "restarting"):
                state.status = "starting"
                self._start_health_monitor(model_id)
            else:
                state.status = "stopped"

        running = sum(1 for s in self.states.values() if s.status == "running")
        print(f"Synced with Docker: {running} running containers", file=sys.stderr)

    def get_catalog(self) -> List[Dict[str, Any]]:
        result = []
        for model_id, cfg in self.catalog.items():
            entry = cfg.to_dict()
            state = self.states[model_id]
            entry["status"] = state.status
            if state.error:
                entry["error"] = state.error
            if state.status == "starting":
                if state.progress is not None:
                    entry["progress"] = state.progress
                if state.progress_msg:
                    entry["progress_msg"] = state.progress_msg
            # Embed cached update info (non-blocking)
            cached_update = self.update_checker.get_cached(model_id)
            entry["update_available"] = cached_update.get("has_update") if cached_update else None
            result.append(entry)
        return result

    def get_running(self) -> List[Dict[str, Any]]:
        result = []
        for model_id, state in self.states.items():
            if state.status in ("running", "starting"):
                entry = state.to_dict()
                cfg = self.catalog.get(model_id)
                if cfg:
                    entry["display_name"] = cfg.display_name
                    entry["port"] = cfg.port
                    entry["backend"] = cfg.backend
                    if cfg.backend == "vllm":
                        entry["gpu_memory"] = cfg.gpu_memory
                result.append(entry)
        return result

    def _gpu_memory_used(self) -> float:
        """Sum of gpu_memory for all running/starting vLLM models."""
        total = 0.0
        for model_id, state in self.states.items():
            if state.status in ("running", "starting"):
                cfg = self.catalog.get(model_id)
                if cfg and cfg.backend == "vllm":
                    total += cfg.gpu_memory
        return total

    def load_model(self, model_id: str, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Load a model by creating and starting an inference container.

        Optional overrides dict:
          llama: ctx_size (int), parallel (int), cache_type (str)
          vllm:  ctx_size (int), max_num_seqs (int), kv_cache_dtype (str)
        """
        with self._lock:
            if model_id not in self.catalog:
                return {"error": f"Unknown model: {model_id}", "status_code": 404}

            cfg = self.catalog[model_id]
            state = self.states[model_id]

            # Already running or starting
            if state.status in ("running", "starting"):
                return {"error": f"Model {model_id} is already {state.status}", "status_code": 409}

            # Concurrency check
            running_count = sum(1 for s in self.states.values() if s.status in ("running", "starting"))
            if MAX_CONCURRENT_MODELS > 0 and running_count >= MAX_CONCURRENT_MODELS:
                return {
                    "error": f"Max concurrent models ({MAX_CONCURRENT_MODELS}) reached. Unload a model first.",
                    "status_code": 409,
                }

            # GPU budget check (vLLM only — llama models don't report gpu_memory fraction)
            if cfg.backend == "vllm":
                used = self._gpu_memory_used()
                if used + cfg.gpu_memory > MAX_GPU_UTILIZATION:
                    return {
                        "error": f"Insufficient GPU memory: {used:.2f} used + {cfg.gpu_memory:.2f} requested > {MAX_GPU_UTILIZATION:.2f} max",
                        "status_code": 400,
                    }

            # Port conflict check
            for mid, s in self.states.items():
                if mid != model_id and s.status in ("running", "starting"):
                    other_cfg = self.catalog.get(mid)
                    if other_cfg and other_cfg.port == cfg.port:
                        return {
                            "error": f"Port {cfg.port} already in use by {mid}",
                            "status_code": 409,
                        }

            # Build container config based on backend
            if cfg.backend == "llama":
                container_config = self._build_llama_config(cfg, overrides)
            else:
                container_config = self._build_vllm_config(cfg, overrides)

            # Remove any stale container with the same name
            container_name = self._container_name(model_id, cfg.backend)
            self.docker.remove_container(container_name, force=True)

            # Create and start container
            status_code, resp = self.docker.create_container(container_name, container_config)
            if status_code not in (201, 200):
                error_msg = resp.get("message", resp.get("raw", f"HTTP {status_code}"))
                print(f"Docker create failed ({status_code}): {resp}", file=sys.stderr)
                state.status = "error"
                state.error = f"Create failed: {error_msg}"
                return {"error": state.error, "status_code": 500}

            container_id = resp.get("Id", "")
            state.container_id = container_id
            state.status = "starting"
            state.error = None
            state.started_at = time.time()

            start_status = self.docker.start_container(container_id)
            if start_status not in (204, 304):
                state.status = "error"
                state.error = f"Start failed: HTTP {start_status}"
                self.docker.remove_container(container_id, force=True)
                return {"error": state.error, "status_code": 500}

            print(f"Container {container_name} started (id={container_id[:12]})", file=sys.stderr)

            # Start background health monitor
            self._start_health_monitor(model_id)

            return {"status": "starting", "model": model_id, "port": cfg.port}

    def _build_llama_config(self, cfg: ModelConfig, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Build Docker container config for a llama.cpp model."""
        ctx_size = cfg.ctx_size
        parallel = cfg.parallel
        cache_type = cfg.cache_type
        if overrides:
            if "ctx_size" in overrides:
                ctx_size = int(overrides["ctx_size"])
            if "parallel" in overrides:
                parallel = int(overrides["parallel"])
            if "cache_type" in overrides:
                cache_type = str(overrides["cache_type"])

        print(f"Loading llama {cfg.id}: ctx={ctx_size} parallel={parallel} cache={cache_type}", file=sys.stderr)

        cmd_parts = [
            "--host", "0.0.0.0",
            "--port", str(cfg.port),
            "--metrics",
            "--poll", "100",
            "--alias", cfg.display_name or cfg.id,
        ]
        # Parse hf:// URLs into --hf-repo and --hf-file flags
        if cfg.model.startswith("hf://"):
            parts = cfg.model[5:].split("/", 2)  # org/repo/file.gguf
            if len(parts) == 3:
                cmd_parts.extend(["--hf-repo", f"{parts[0]}/{parts[1]}", "--hf-file", parts[2]])
            else:
                cmd_parts.extend(["-m", cfg.model])
        else:
            cmd_parts.extend(["-m", cfg.model])
        if ctx_size:
            cmd_parts.extend(["--ctx-size", str(ctx_size)])
        if cfg.tensor_split:
            cmd_parts.extend(["--tensor-split", cfg.tensor_split])
        cmd_parts.extend(["--parallel", str(parallel)])
        if parallel > 1:
            cmd_parts.append("--kv-unified")
        if cache_type and cache_type != "f16":
            cmd_parts.extend(["--cache-type-k", cache_type, "--cache-type-v", cache_type])
        cmd_parts.extend(["--flash-attn", "on"])

        return {
            "Image": LLAMA_IMAGE,
            "Entrypoint": ["/app/llama-server"],
            "Cmd": cmd_parts,
            "Labels": {
                MANAGER_LABEL: MANAGER_VALUE,
                MODEL_LABEL: cfg.id,
            },
            "Healthcheck": {
                "Test": ["CMD", "curl", "-f", f"http://localhost:{cfg.port}/health"],
                "Interval": 10_000_000_000,  # 10s in nanoseconds
                "Timeout": 5_000_000_000,
                "Retries": 3,
            },
            "ExposedPorts": {f"{cfg.port}/tcp": {}},
            "HostConfig": {
                "Binds": list(LLAMA_VOLUME_MOUNTS),
                "PortBindings": {
                    f"{cfg.port}/tcp": [{"HostPort": str(cfg.port)}],
                },
                "DeviceRequests": [
                    {
                        "Driver": "",
                        "Count": -1,
                        "DeviceIDs": [],
                        "Capabilities": [["gpu"]],
                        "Options": {},
                    }
                ],
                "RestartPolicy": {"Name": "unless-stopped"},
            },
        }

    def _build_vllm_config(self, cfg: ModelConfig, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Build Docker container config for a vLLM model."""
        max_model_len = cfg.max_model_len
        max_num_seqs = cfg.max_num_seqs
        kv_cache_dtype = cfg.kv_cache_dtype
        if overrides:
            if "ctx_size" in overrides:
                max_model_len = int(overrides["ctx_size"])
            if "max_num_seqs" in overrides:
                max_num_seqs = int(overrides["max_num_seqs"])
            if "kv_cache_dtype" in overrides:
                kv_cache_dtype = str(overrides["kv_cache_dtype"])

        print(f"Loading vllm {cfg.id}: ctx={max_model_len} seqs={max_num_seqs} kv_dtype={kv_cache_dtype}", file=sys.stderr)

        cmd_parts = [
            "vllm", "serve", cfg.model,
            "--served-model-name", cfg.served_model_name,
            "--max-model-len", str(max_model_len),
            "--max-num-seqs", str(max_num_seqs),
            "--gpu-memory-utilization", str(cfg.gpu_memory),
            "-tp", str(cfg.tp),
            "--host", "0.0.0.0",
            "--port", str(cfg.port),
        ]
        if kv_cache_dtype != "auto":
            cmd_parts.extend(["--kv-cache-dtype", kv_cache_dtype])
        if cfg.max_num_batched_tokens:
            cmd_parts.extend(["--max-num-batched-tokens", cfg.max_num_batched_tokens])
        if cfg.vllm_args:
            cmd_parts.extend(cfg.vllm_args.split())

        # If mods are configured, wrap the command to apply patches first
        binds = list(VLLM_VOLUME_MOUNTS)
        if cfg.vllm_mods:
            mod_names = cfg.vllm_mods.split()
            mod_scripts = []
            for mod_name in mod_names:
                host_path = os.path.join(VLLM_MODS_DIR, mod_name)
                container_path = f"/mods/{mod_name}"
                binds.append(f"{host_path}:{container_path}:ro")
                mod_scripts.append(f"cd {container_path} && bash run.sh")
            # Wrap: apply mods then exec the original command
            vllm_cmd = " ".join(cmd_parts)
            mod_chain = " && ".join(mod_scripts)
            cmd_parts = ["bash", "-c", f"{mod_chain} && exec {vllm_cmd}"]

        return {
            "Image": VLLM_IMAGE,
            "Cmd": cmd_parts,
            "Env": ["NCCL_IGNORE_CPU_AFFINITY=1"] + (cfg.vllm_env.split() if cfg.vllm_env else []),
            "Labels": {
                MANAGER_LABEL: MANAGER_VALUE,
                MODEL_LABEL: cfg.id,
            },
            "Healthcheck": {
                "Test": ["CMD", "curl", "-f", f"http://localhost:{cfg.port}/health"],
                "Interval": 10_000_000_000,
                "Timeout": 5_000_000_000,
                "Retries": 3,
            },
            "ExposedPorts": {f"{cfg.port}/tcp": {}},
            "HostConfig": {
                "Binds": binds,
                "PortBindings": {
                    f"{cfg.port}/tcp": [{"HostPort": str(cfg.port)}],
                },
                "IpcMode": "host",
                "DeviceRequests": [
                    {
                        "Driver": "",
                        "Count": -1,
                        "DeviceIDs": [],
                        "Capabilities": [["gpu"]],
                        "Options": {},
                    }
                ],
                "RestartPolicy": {"Name": "unless-stopped"},
            },
        }

    def unload_model(self, model_id: str) -> Dict[str, Any]:
        """Unload a model by stopping and removing its container."""
        with self._lock:
            if model_id not in self.catalog:
                return {"error": f"Unknown model: {model_id}", "status_code": 404}

            state = self.states[model_id]
            if state.status == "stopped":
                return {"error": f"Model {model_id} is not running", "status_code": 400}

            cfg = self.catalog[model_id]
            container_name = self._container_name(model_id, cfg.backend)

            # Stop container
            if state.container_id:
                self.docker.stop_container(state.container_id, timeout=10)
                self.docker.remove_container(state.container_id, force=True)
            else:
                self.docker.stop_container(container_name, timeout=10)
                self.docker.remove_container(container_name, force=True)

            state.status = "stopped"
            state.container_id = None
            state.started_at = None
            state.error = None

            print(f"Container {container_name} stopped and removed", file=sys.stderr)
            return {"status": "stopped", "model": model_id}

    def get_model_logs(self, model_id: str, tail: int = 200) -> Dict[str, Any]:
        """Get container logs for a model."""
        cfg = self.catalog.get(model_id)
        if not cfg:
            return {"error": f"Unknown model: {model_id}", "status_code": 404}

        state = self.states.get(model_id)
        container_id = state.container_id if state else None
        container_name = self._container_name(model_id, cfg.backend)

        target = container_id or container_name
        logs = self.docker.get_container_logs(target, tail=tail)

        return {
            "model": model_id,
            "status": state.status if state else "unknown",
            "logs": logs,
        }

    def update_model(self, model_id: str) -> Dict[str, Any]:
        """Full update cycle: unload → delete cache → invalidate → reload."""
        if model_id not in self.catalog:
            return {"error": f"Unknown model: {model_id}", "status_code": 404}

        cfg = self.catalog[model_id]
        state = self.states[model_id]

        # 1. Unload if running
        if state.status in ("running", "starting"):
            result = self.unload_model(model_id)
            if "error" in result:
                return result

        # 2. Delete local cache
        try:
            self._delete_model_cache(cfg)
        except Exception as e:
            return {"error": f"Failed to clear cache: {e}", "status_code": 500}

        # 3. Invalidate update checker cache
        self.update_checker.invalidate(model_id)

        # 4. Reload (container will re-download from HF)
        result = self.load_model(model_id)
        if "error" in result:
            return result

        return {"status": "updating", "model": model_id, "message": "Cache cleared, model reloading"}

    def _delete_model_cache(self, cfg: "ModelConfig"):
        """Delete cached model files so container re-downloads from HF."""
        if cfg.backend == "llama":
            if not LLAMA_CACHE_DIR:
                return
            if not cfg.model.startswith("hf://"):
                return
            parts = cfg.model[5:].split("/", 2)
            if len(parts) != 3:
                return
            org, repo, filename = parts
            cache_name = f"{org}_{repo}_{filename}"
            for suffix in ("", ".etag"):
                path = os.path.join(LLAMA_CACHE_DIR, f"{cache_name}{suffix}")
                if os.path.exists(path):
                    os.remove(path)
                    print(f"Deleted cache file: {path}", file=sys.stderr)
        else:
            if not HF_CACHE_DIR:
                return
            org_repo = cfg.model
            if "/" not in org_repo:
                return
            org, repo = org_repo.split("/", 1)
            cache_dir = os.path.join(HF_CACHE_DIR, f"models--{org}--{repo}")
            if os.path.isdir(cache_dir):
                shutil.rmtree(cache_dir)
                print(f"Deleted cache directory: {cache_dir}", file=sys.stderr)

    def _start_health_monitor(self, model_id: str):
        """Start a background thread to poll container health for a starting model."""
        if model_id in self._health_threads:
            t = self._health_threads[model_id]
            if t.is_alive():
                return

        t = threading.Thread(target=self._health_poll, args=(model_id,), daemon=True)
        self._health_threads[model_id] = t
        t.start()

    # Regex patterns for progress scraping
    _RE_VLLM_SHARDS = re.compile(
        r"Loading safetensors[^:]*:\s*(\d+)% Completed \| (\d+)/(\d+)"
    )
    _RE_VLLM_CUDA_GRAPHS = re.compile(
        r"Capturing CUDA graphs.*?(\d+)%.*?(\d+)/(\d+)"
    )
    _RE_LLAMA_MODEL = re.compile(r"\[PROGRESS\] Model loading: ([\d.]+)%")
    _RE_LLAMA_CTX = re.compile(r"\[PROGRESS\] Context init: ([\d.]+)%.*total: ([\d.]+)%")

    def _scrape_progress(self, model_id: str):
        """Scrape container logs for startup progress. Updates state in-place."""
        state = self.states.get(model_id)
        cfg = self.catalog.get(model_id)
        if not state or not cfg or not state.container_id:
            return

        logs = self.docker.get_container_logs(state.container_id, tail=30, timeout=3)
        if not logs:
            return

        if cfg.backend == "vllm":
            # Phase 1: Loading weights (maps to 0-70% overall)
            last_shard = None
            for m in self._RE_VLLM_SHARDS.finditer(logs):
                last_shard = (int(m.group(1)), m.group(2), m.group(3))

            # Phase 2: CUDA graph capture (maps to 70-95% overall)
            last_cuda = None
            for m in self._RE_VLLM_CUDA_GRAPHS.finditer(logs):
                last_cuda = (int(m.group(1)), m.group(2), m.group(3))

            if last_cuda is not None:
                pct, done, total = last_cuda
                overall = 70 + int(pct * 0.25)
                state.progress = min(overall, 95)
                state.progress_msg = f"Compiling CUDA graphs {done}/{total}"
            elif last_shard is not None:
                pct, done, total = last_shard
                if pct >= 100:
                    # Weights loaded, waiting for CUDA graph compilation to start
                    state.progress = 70
                    state.progress_msg = "Compiling model..."
                else:
                    overall = int(pct * 0.7)
                    state.progress = overall
                    state.progress_msg = f"Loading weights {done}/{total}"

        elif cfg.backend == "llama":
            # Two phases: "Model loading: XX%" then "Context init: XX% (total: YY%)"
            last_model = None
            last_ctx = None
            for m in self._RE_LLAMA_MODEL.finditer(logs):
                last_model = float(m.group(1))
            for m in self._RE_LLAMA_CTX.finditer(logs):
                last_ctx = (float(m.group(1)), float(m.group(2)))

            if last_ctx is not None:
                # Context init phase — map raw 0-100% to overall 80-100%
                overall = 80 + int(last_ctx[0] * 0.2)
                state.progress = min(overall, 100)
                state.progress_msg = f"Initializing context {last_ctx[0]:.0f}%"
            elif last_model is not None:
                # Model loading phase — map 0-100% to overall 0-80%
                state.progress = int(last_model * 0.8)
                state.progress_msg = f"Loading tensors {last_model:.0f}%"

    @staticmethod
    def _http_get(host: str, port: int, path: str, timeout: int = 3) -> Optional[dict]:
        """Simple HTTP GET returning parsed JSON body, or None on failure."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
            req = f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n"
            sock.sendall(req.encode())
            resp = sock.recv(4096)
            body_start = resp.find(b"\r\n\r\n")
            if body_start != -1:
                return json.loads(resp[body_start + 4:])
        except Exception:
            return None
        finally:
            sock.close()
        return None

    def _health_poll(self, model_id: str):
        """Poll health endpoint until ready or timeout."""
        cfg = self.catalog.get(model_id)
        state = self.states.get(model_id)
        if not cfg or not state:
            return

        start_time = time.time()

        # Readiness endpoint differs by backend
        # llama: /health returns 200 immediately (even during loading) with load_progress
        #        /props returns 503 until fully ready, then 200
        # vllm:  /health returns 503 until ready, then 200
        if cfg.backend == "llama":
            ready_path = "/props"
            progress_path = "/health"
        else:
            ready_path = "/health"
            progress_path = None

        while state.status == "starting":
            elapsed = time.time() - start_time

            # Check if container is still alive
            if state.container_id:
                info = self.docker.inspect_container(state.container_id)
                container_state = info.get("State", {})
                if not container_state.get("Running", False):
                    exit_code = container_state.get("ExitCode", -1)
                    error_msg = container_state.get("Error", "")
                    oom = container_state.get("OOMKilled", False)
                    if oom:
                        state.error = "Container killed: Out of GPU memory (OOM)"
                    elif error_msg:
                        state.error = f"Container exited (code {exit_code}): {error_msg}"
                    else:
                        state.error = f"Container exited with code {exit_code}"
                    state.status = "error"
                    print(f"Health monitor: {model_id} failed - {state.error}", file=sys.stderr)
                    return

            # Scrape progress from container logs (works for both backends)
            self._scrape_progress(model_id)

            # For llama: also check /health for load_progress (more granular during tensor loading)
            if progress_path:
                try:
                    resp = self._http_get("localhost", cfg.port, progress_path)
                    if resp and resp.get("load_progress") is not None:
                        lp = resp["load_progress"]
                        # load_progress covers 0-100% of model load (maps to 0-80% overall)
                        pct = int(lp * 80)
                        if pct > (state.progress or 0):
                            state.progress = pct
                            state.progress_msg = f"Loading tensors {int(lp * 100)}%"
                except Exception:
                    pass

            # Check readiness
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(3)
                sock.connect(("localhost", cfg.port))
                request = f"GET {ready_path} HTTP/1.1\r\nHost: localhost:{cfg.port}\r\nConnection: close\r\n\r\n"
                sock.sendall(request.encode())
                response = sock.recv(4096)
                sock.close()

                if b"200" in response.split(b"\r\n")[0]:
                    state.status = "running"
                    state.progress = None
                    state.progress_msg = None
                    print(f"Health monitor: {model_id} is ready (took {elapsed:.0f}s)", file=sys.stderr)
                    return

            except (ConnectionRefusedError, OSError, socket.timeout):
                pass

            # Timeout
            if elapsed > STARTUP_TIMEOUT:
                state.status = "error"
                state.error = f"Startup timeout after {STARTUP_TIMEOUT}s"
                print(f"Health monitor: {model_id} timed out", file=sys.stderr)
                return

            time.sleep(HEALTH_POLL_INTERVAL)

    def get_health(self) -> Dict[str, Any]:
        """Service health check."""
        running = sum(1 for s in self.states.values() if s.status == "running")
        starting = sum(1 for s in self.states.values() if s.status == "starting")
        result: Dict[str, Any] = {
            "status": "ok",
            "models_running": running,
            "models_starting": starting,
            "max_concurrent_models": MAX_CONCURRENT_MODELS,
        }

        # Report image availability for backends present in catalog
        backends_present = set(c.backend for c in self.catalog.values())
        if "llama" in backends_present:
            result["llama_image"] = LLAMA_IMAGE
            result["llama_image_available"] = self.docker.image_exists(LLAMA_IMAGE)
        if "vllm" in backends_present:
            result["vllm_image"] = VLLM_IMAGE
            result["vllm_image_available"] = self.docker.image_exists(VLLM_IMAGE)
            result["gpu_memory_used"] = self._gpu_memory_used()
            result["gpu_memory_max"] = MAX_GPU_UTILIZATION

        return result


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------
manager: Optional[ModelManager] = None


class ManagerHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self._json_response(200, manager.get_health())
            return

        if not self._check_auth():
            self._json_response(401, {"error": "Unauthorized"})
            return

        if self.path == "/models/catalog":
            self._json_response(200, manager.get_catalog())
            return

        if self.path == "/models/running":
            self._json_response(200, manager.get_running())
            return

        if self.path == "/models/updates":
            results = manager.update_checker.check_all(manager.catalog)
            self._json_response(200, results)
            return

        # /models/<id>/logs?tail=200
        if self.path.startswith("/models/") and "/logs" in self.path:
            parts = self.path.split("?")[0].rstrip("/").split("/")
            if len(parts) == 4 and parts[3] == "logs":
                model_id = urllib.parse.unquote(parts[2])
                tail = 200
                if "?" in self.path:
                    qs = urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    try:
                        tail = int(qs.get("tail", ["200"])[0])
                    except ValueError:
                        pass
                result = manager.get_model_logs(model_id, tail=min(tail, 2000))
                code = result.pop("status_code", 200)
                self._json_response(code, result)
                return

        self._json_response(404, {"error": "Not found"})

    def do_POST(self):
        if not self._check_auth():
            self._json_response(401, {"error": "Unauthorized"})
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = {}
        if content_length > 0:
            raw = self.rfile.read(content_length)
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                self._json_response(400, {"error": "Invalid JSON"})
                return

        if self.path == "/models/load":
            model_id = body.get("model", "")
            if not model_id:
                self._json_response(400, {"error": "Missing 'model' field"})
                return
            # Extract optional parameter overrides (both backends)
            overrides = {}
            for key in ("ctx_size", "parallel", "cache_type", "max_num_seqs", "kv_cache_dtype"):
                if key in body:
                    overrides[key] = body[key]
            result = manager.load_model(model_id, overrides if overrides else None)
            code = result.pop("status_code", 200)
            self._json_response(code, result)
            return

        if self.path == "/models/unload":
            model_id = body.get("model", "")
            if not model_id:
                self._json_response(400, {"error": "Missing 'model' field"})
                return
            result = manager.unload_model(model_id)
            code = result.pop("status_code", 200)
            self._json_response(code, result)
            return

        if self.path == "/models/updates/refresh":
            model_id = body.get("model")
            if model_id:
                # Refresh a single model
                if model_id not in manager.catalog:
                    self._json_response(404, {"error": f"Unknown model: {model_id}"})
                    return
                manager.update_checker.invalidate(model_id)
                result = manager.update_checker.check_model(model_id, manager.catalog[model_id])
                self._json_response(200, result)
            else:
                # Refresh all models
                manager.update_checker.invalidate()
                results = manager.update_checker.check_all(manager.catalog)
                self._json_response(200, results)
            return

        if self.path == "/models/update":
            model_id = body.get("model", "")
            if not model_id:
                self._json_response(400, {"error": "Missing 'model' field"})
                return
            result = manager.update_model(model_id)
            code = result.pop("status_code", 200)
            self._json_response(code, result)
            return

        self._json_response(404, {"error": "Not found"})

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def _check_auth(self) -> bool:
        return True  # Trusted LAN — no auth needed

    def _json_response(self, code: int, data: Any):
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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def log_message(self, fmt, *args):
        print(fmt % args, file=sys.stderr)


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    global manager
    manager = ModelManager()

    def shutdown(sig, frame):
        print("\nShutting down...", file=sys.stderr)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    server = ThreadedHTTPServer(("0.0.0.0", PORT), ManagerHandler)
    print(f"model-manager listening on port {PORT} ({len(manager.catalog)} models in catalog)", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
