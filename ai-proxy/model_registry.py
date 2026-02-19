"""
model_registry.py — Discovers models from model-manager catalog APIs.

Replaces the static models.ini approach. Configured via MODEL_MANAGERS env var
(comma-separated host:port pairs) or ELLIE_MANAGER_HOST / SPARKY_MANAGER_HOST.
Each model manager exposes GET /models/catalog returning a JSON array of model
configs. The registry derives each model's inference host from the manager's
host IP + the model's port field.
"""

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.request import urlopen, Request
from urllib.error import URLError

logger = logging.getLogger(__name__)

# How often to refresh the catalog from model managers (seconds)
CATALOG_REFRESH_INTERVAL = int(os.environ.get("CATALOG_REFRESH_INTERVAL", "30"))

# Timeout for catalog HTTP requests (seconds)
CATALOG_FETCH_TIMEOUT = int(os.environ.get("CATALOG_FETCH_TIMEOUT", "5"))

# Default host for models without an explicit host (used as fallback only)
DEFAULT_HOST = os.environ.get("DEFAULT_MODEL_HOST", "172.17.0.1:8082")


@dataclass
class ModelConfig:
    id: str
    name: str  # alias / display name
    path: str  # HF model path
    backend: str  # 'llama' | 'vllm'
    host: str = ""  # hostname:port for inference requests
    parameters: Dict[str, str] = field(default_factory=dict)

    @property
    def safe_id(self):
        return self.id.replace(" ", "_").lower()

    @property
    def base_url(self) -> str:
        """Full base URL for OpenAI-compatible API on this model's host."""
        return f"http://{self.host}/v1"

    @property
    def is_local(self) -> bool:
        """True if this model runs on a local host (Docker bridge gateway or localhost)."""
        h = self.host.split(":")[0]
        return h in ("llama-server", "localhost", "127.0.0.1", "172.17.0.1")


class ModelRegistry:
    """Discovers models from model-manager catalog APIs.

    Configured via manager_endpoints parameter or MODEL_MANAGERS env var.
    Falls back to ELLIE_MANAGER_HOST + SPARKY_MANAGER_HOST if MODEL_MANAGERS
    is not set.

    Each model manager exposes GET /models/catalog returning a JSON array.
    The registry derives each model's inference host from the manager's
    host IP + the model's port field.
    """

    def __init__(self, manager_endpoints: Optional[List[str]] = None):
        if manager_endpoints is not None:
            self.manager_endpoints = list(manager_endpoints)
        else:
            raw = os.environ.get("MODEL_MANAGERS", "")
            if raw.strip():
                self.manager_endpoints = [ep.strip() for ep in raw.split(",") if ep.strip()]
            else:
                # Fall back to individual env vars
                endpoints = []
                for var in ("ELLIE_MANAGER_HOST", "SPARKY_MANAGER_HOST"):
                    val = os.environ.get(var, "")
                    if val:
                        endpoints.append(val)
                self.manager_endpoints = endpoints

        self.models: Dict[str, ModelConfig] = {}
        self._lock = threading.Lock()
        self._refresh_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        if not self.manager_endpoints:
            logger.warning("No model manager endpoints configured — registry will be empty")
        else:
            logger.info(f"Model manager endpoints: {self.manager_endpoints}")

    # ------------------------------------------------------------------
    # Public API (same interface as before)
    # ------------------------------------------------------------------

    def load_models(self) -> Dict[str, ModelConfig]:
        """Fetch catalogs from all configured model managers and rebuild registry."""
        new_models: Dict[str, ModelConfig] = {}

        for endpoint in self.manager_endpoints:
            try:
                catalog = self._fetch_catalog(endpoint)
                manager_host = endpoint.split(":")[0]

                for entry in catalog:
                    model = self._catalog_entry_to_config(entry, manager_host)
                    if model:
                        new_models[model.id] = model
                        logger.info(
                            f"Discovered model: {model.id} -> {model.backend} @ {model.host} "
                            f"(Name: {model.name}) from {endpoint}"
                        )

            except Exception as e:
                logger.error(f"Failed to fetch catalog from {endpoint}: {e}")

        if new_models:
            with self._lock:
                self.models = new_models
            logger.info(f"Model registry refreshed: {len(new_models)} models from {len(self.manager_endpoints)} manager(s)")
        elif not self.models:
            logger.warning("No models discovered and no cached models available")
        else:
            logger.warning("Catalog fetch returned no models — keeping cached registry")

        return self.models

    def get_model(self, model_id: str) -> Optional[ModelConfig]:
        if not self.models:
            self.load_models()
        with self._lock:
            return self.models.get(model_id)

    def find_model(self, name: str) -> Optional[ModelConfig]:
        """Find a model by id, alias, served_model_name, or HF path (case-insensitive).

        Also handles llama.cpp HF-cached filenames where slashes become
        underscores (e.g. 'org_repo_file.gguf' matches 'hf://org/repo/file.gguf').
        """
        if not self.models:
            self.load_models()

        name_lower = name.lower()
        with self._lock:
            for m in self.models.values():
                candidates = [m.id.lower(), m.name.lower(), m.path.lower()]
                # Also match served_model_name if present (vLLM)
                served = m.parameters.get("served_model_name", "")
                if served:
                    candidates.append(served.lower())
                if name_lower in candidates:
                    return m

            # Fallback: match HF cache filenames (org_repo_file.gguf → hf://org/repo/file.gguf)
            name_norm = name_lower.replace("/", "_").replace(":", "_").lstrip("_")
            for m in self.models.values():
                path_norm = m.path.lower().replace("hf://", "").replace("/", "_")
                if name_norm == path_norm:
                    return m

        return None

    def list_models(self) -> List[ModelConfig]:
        if not self.models:
            self.load_models()
        with self._lock:
            return list(self.models.values())

    # ------------------------------------------------------------------
    # Background refresh
    # ------------------------------------------------------------------

    def start_refresh_loop(self):
        """Start a daemon thread that periodically refreshes the catalog."""
        if self._refresh_thread and self._refresh_thread.is_alive():
            return
        self._stop_event.clear()
        self._refresh_thread = threading.Thread(target=self._refresh_loop, daemon=True)
        self._refresh_thread.start()
        logger.info(f"Catalog refresh thread started (interval={CATALOG_REFRESH_INTERVAL}s)")

    def stop_refresh_loop(self):
        self._stop_event.set()

    def _refresh_loop(self):
        while not self._stop_event.is_set():
            self._stop_event.wait(CATALOG_REFRESH_INTERVAL)
            if self._stop_event.is_set():
                break
            try:
                self.load_models()
            except Exception as e:
                logger.error(f"Catalog refresh error: {e}")

    # ------------------------------------------------------------------
    # Catalog fetching
    # ------------------------------------------------------------------

    def _fetch_catalog(self, endpoint: str) -> list:
        """Fetch /models/catalog from a model manager endpoint."""
        url = f"http://{endpoint}/models/catalog"
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=CATALOG_FETCH_TIMEOUT) as resp:
            data = json.loads(resp.read())
        if not isinstance(data, list):
            raise ValueError(f"Expected list from {url}, got {type(data).__name__}")
        return data

    def _catalog_entry_to_config(self, entry: dict, manager_host: str) -> Optional[ModelConfig]:
        """Convert a catalog JSON entry into a ModelConfig.

        Maps catalog fields to the ModelConfig/parameters format expected by proxy.py.
        Proxy reads sampling defaults from parameters with hyphenated keys
        (e.g. 'default-temperature'), so we normalize underscores to hyphens.
        """
        model_id = entry.get("id", "")
        if not model_id:
            return None

        port = entry.get("port", 8000)
        host = f"{manager_host}:{port}"
        backend = entry.get("backend", "vllm")
        # Normalize backend names
        if backend == "llama.cpp":
            backend = "llama"

        display_name = entry.get("display_name", model_id)
        model_path = entry.get("model", "")

        # Build parameters dict with hyphenated keys (proxy.py convention)
        # Include all fields that proxy.py reads from parameters
        params: Dict[str, str] = {}

        # Context / VRAM fields
        for key in ("ctx_size", "max_ctx", "parallel", "cache_type",
                     "vram_base", "vram_per_1k_ctx", "tensor_split",
                     "served_model_name", "gpu_memory", "tp",
                     "max_model_len", "max_num_seqs", "kv_cache_dtype"):
            if key in entry:
                # Convert underscores to hyphens for proxy.py compatibility
                params[key.replace("_", "-")] = str(entry[key])

        # Proxy routing fields (sampling defaults, output cap, visibility)
        for key in ("default_temperature", "default_top_p", "default_top_k",
                     "default_min_p", "max_output", "chat_visible"):
            if key in entry:
                params[key.replace("_", "-")] = str(entry[key])

        return ModelConfig(
            id=model_id,
            name=display_name,
            path=model_path,
            backend=backend,
            host=host,
            parameters=params,
        )
