# Multi-Host vLLM Routing — Ellie Deployment Instructions

Sparky (10.20.10.10) has been stripped to just vllm-server. Now ellie needs her
ai-proxy code updated so it routes inference requests to the correct vLLM host
based on the model requested. Ellie keeps all infrastructure (Supabase, ai-proxy,
temper-view, stripe-handler) and her own local vllm-server.

## Overview of changes

4 files need to be modified in ~/ai-stack:

1. `models.ini` — add `host = hostname:port` to each model
2. `ai-proxy/model_registry.py` — add host field, base_url property, find_model()
3. `ai-proxy/proxy.py` — per-model routing instead of global BACKENDS dict
4. `ai-proxy/model_manager.py` — remote models always available, list includes host

After changes: `docker compose up -d --build ai-proxy`

---

## 1. models.ini — replace entire file

```ini
[Nemotron 3 Nano]
alias = Nemotron 3 Nano
model = nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4
backend = vllm
host = 10.20.10.10:8000
ctx-size = 131072

[GLM 4.7 Flash]
alias = GLM 4.7 Flash
model = GadflyII/GLM-4.7-Flash-MTP-NVFP4
backend = vllm
host = vllm-server:8000
ctx-size = 65536

[Qwen3 Coder Next]
alias = Qwen3 Coder Next
model = GadflyII/Qwen3-Coder-Next-NVFP4
backend = vllm
host = vllm-server:8000
ctx-size = 131072
```

Key point: `host = 10.20.10.10:8000` means sparky (remote). `host = vllm-server:8000` means ellie's local vllm container.

---

## 2. ai-proxy/model_registry.py — replace entire file

```python
import configparser
import os
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Default vLLM host from env vars (used when model has no explicit host)
DEFAULT_VLLM_HOST = os.environ.get("VLLM_SERVER_HOST", "vllm-server")
DEFAULT_VLLM_PORT = os.environ.get("VLLM_SERVER_PORT", "8000")

@dataclass
class ModelConfig:
    id: str
    name: str  # alias
    path: str
    backend: str  # 'llama' | 'vllm'
    host: str = ""  # hostname:port
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
        """True if this model runs on the local vllm-server container."""
        h = self.host.split(":")[0]
        return h in ("vllm-server", "localhost", "127.0.0.1")

class ModelRegistry:
    def __init__(self, config_path: str = "/app/models.ini"):
        self.config_path = config_path
        self.models: Dict[str, ModelConfig] = {}
        self.last_loaded = 0

    def load_models(self) -> Dict[str, ModelConfig]:
        """Parses models.ini and returns a dictionary of ModelConfigs"""
        if not os.path.exists(self.config_path):
            logger.error(f"Models config not found at {self.config_path}")
            return {}

        try:
            config = configparser.ConfigParser()
            config.read(self.config_path)

            new_models = {}
            for section in config.sections():
                model_id = section

                # Extract core fields
                path = config[section].get('model', model_id)
                alias = config[section].get('alias', model_id)
                host = config[section].get('host', f"{DEFAULT_VLLM_HOST}:{DEFAULT_VLLM_PORT}")

                # Determine backend
                if 'backend' in config[section]:
                    backend = config[section]['backend']
                elif path.strip().endswith('.gguf'):
                    backend = 'llama'
                else:
                    backend = 'vllm'

                # Extract other parameters (context size, etc.)
                params = {}
                for key, value in config[section].items():
                    if key not in ('model', 'alias', 'backend', 'host'):
                        params[key] = value

                model_config = ModelConfig(
                    id=model_id,
                    name=alias,
                    path=path,
                    backend=backend,
                    host=host,
                    parameters=params
                )

                new_models[model_id] = model_config
                logger.info(f"Loaded model config: {model_id} -> {backend} @ {host} (Alias: {alias})")

            self.models = new_models
            return self.models

        except Exception as e:
            logger.error(f"Error parse models.ini: {e}")
            return {}

    def get_model(self, model_id: str) -> Optional[ModelConfig]:
        if not self.models:
            self.load_models()
        return self.models.get(model_id)

    def find_model(self, name: str) -> Optional[ModelConfig]:
        """Find a model by id, alias, or HF path (case-insensitive)."""
        if not self.models:
            self.load_models()

        name_lower = name.lower()
        for m in self.models.values():
            if name_lower in (m.id.lower(), m.name.lower(), m.path.lower()):
                return m
        return None

    def list_models(self) -> List[ModelConfig]:
        if not self.models:
            self.load_models()
        return list(self.models.values())
```

---

## 3. ai-proxy/proxy.py — two targeted edits

### Edit A: Replace the BACKENDS dict (around line 38-49)

Find this block:
```python
# Backend Configuration
BACKENDS = {
    "llama": {
        "url": f"http://{LLAMA_SERVER_HOST}:{LLAMA_SERVER_PORT}{LLAMA_API_PREFIX}",
        "name": "llama-server",
        "key": "llama"
    },
    "vllm": {
        "url": f"http://{VLLM_SERVER_HOST}:{VLLM_SERVER_PORT}/v1",
        "name": "vllm-server",
        "key": "vllm"
    }
}
```

Replace with:
```python
# Legacy fallback backend URL (used only if no model config found)
FALLBACK_VLLM_URL = f"http://{VLLM_SERVER_HOST}:{VLLM_SERVER_PORT}/v1"
```

### Edit B: Replace the routing logic section

Find the block that starts with `# Routing Logic` and contains the `BACKENDS[backend_key]` references (the section between "Read request body" and "Proxy Forwarding" log line). Replace the entire routing block — from `# Routing Logic` through the line that sets `url = f"{backend_url}{forward_path}"` — with:

```python
                # Routing Logic — resolve model from request body
                is_streaming = False
                model_config = None

                if body and self.headers.get('Content-Type') == 'application/json':
                    try:
                        data = json.loads(body)
                        requested_model = data.get('model', '')
                        is_streaming = data.get('stream', False)

                        # Look up model in registry by id, alias, or HF path
                        if requested_model:
                            model_config = model_registry.find_model(requested_model)

                        # Fall back to the currently-loaded local model
                        if not model_config:
                            state = model_manager.get_status()
                            cid = state.get('current_model_id')
                            if cid:
                                model_config = model_registry.get_model(cid)

                        if not model_config:
                            self.send_error_json(503, "No model found for request")
                            return

                        # Rewrite model field to the full HF path (vLLM requirement)
                        if model_config.backend == 'vllm':
                            data['model'] = model_config.path

                        body = json.dumps(data).encode('utf-8')

                    except Exception as e:
                        print(f"Proxy Error parsing JSON: {e}", file=sys.stderr)

                # For non-JSON requests (e.g. GET /v1/models), fall back to current model
                if not model_config:
                    state = model_manager.get_status()
                    cid = state.get('current_model_id')
                    if cid:
                        model_config = model_registry.get_model(cid)

                if not model_config:
                    self.send_error_json(503, "No model loaded")
                    return

                # Build target URL from per-model host
                backend_url = model_config.base_url

                # Strip /v1 prefix from path since base_url already includes it
                forward_path = self.path
                if self.path.startswith('/v1'):
                    forward_path = self.path[3:]  # remove '/v1'

                url = f"{backend_url}{forward_path}"
```

### Edit C: Update /model/list response to include host fields

In the `/model/list` handler, the compat_models append block should become:

```python
                            compat_models = []
                            for m in models:
                                compat_models.append({
                                    "name": m["id"],
                                    "alias": m["name"],
                                    "backend": m["backend"],
                                    "host": m["host"],
                                    "is_local": m["is_local"],
                                    "status": "active" if m["is_active"] else "ready"
                                })
```

---

## 4. ai-proxy/model_manager.py — replace entire file

```python
import logging
import time
from enum import Enum
from typing import Optional, Dict, Any
from model_registry import ModelRegistry, ModelConfig
from backend_manager import BackendManager

logger = logging.getLogger(__name__)

class ModelStatus(Enum):
    OFFLINE = "offline"
    STARTING = "starting"
    LOADING = "loading"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"

class ModelManager:
    def __init__(self, registry: ModelRegistry, backend_manager: BackendManager):
        self.registry = registry
        self.backend_manager = backend_manager

        # State (tracks the locally-loaded model only)
        self.current_model_id: Optional[str] = None
        self.target_model_id: Optional[str] = None
        self.status: ModelStatus = ModelStatus.OFFLINE
        self.last_error: Optional[str] = None
        self.state_changed_at: float = time.time()

        # Initialize registry
        self.registry.load_models()

    def get_status(self) -> Dict[str, Any]:
        """Return the current state of the model manager"""
        current_config = self.registry.get_model(self.current_model_id) if self.current_model_id else None

        return {
            "status": self.status.value,
            "current_model_id": self.current_model_id,
            "target_model_id": self.target_model_id,
            "current_model": {
                "id": current_config.id,
                "name": current_config.name,
                "backend": current_config.backend,
                "host": current_config.host,
            } if current_config else None,
            "last_error": self.last_error,
            "uptime_seconds": time.time() - self.state_changed_at if self.status == ModelStatus.RUNNING else 0
        }

    def list_models(self):
        """Return list of available models with host info."""
        models = []
        for m in self.registry.list_models():
            is_active = (m.id == self.current_model_id) if m.is_local else True
            models.append({
                "id": m.id,
                "name": m.name,
                "backend": m.backend,
                "path": m.path,
                "host": m.host,
                "is_local": m.is_local,
                "is_active": is_active,
            })
        return models

    def switch_model(self, model_id: str) -> bool:
        """Initiate valid state transition to switch model (local models only)."""
        logger.info(f"Requested switch to model: {model_id}")

        # 1. Validate Model
        config = self.registry.get_model(model_id)
        if not config:
            self._set_error(f"Model {model_id} not found in registry")
            return False

        # Remote models can't be switched from here
        if not config.is_local:
            logger.info(f"Model {model_id} is remote ({config.host}), no local switch needed.")
            return True

        # 2. Check if already running
        if self.current_model_id == model_id and self.status == ModelStatus.RUNNING:
            logger.info(f"Model {model_id} is already running.")
            return True

        # 3. Update State
        self._set_status(ModelStatus.STARTING)
        self.target_model_id = model_id
        self.last_error = None

        try:
            # 4. Delegate to BackendManager
            success = self.backend_manager.switch_backend(config.backend, config.path)

            if success:
                self.current_model_id = model_id
                self._set_status(ModelStatus.RUNNING)
                self.target_model_id = None
                return True
            else:
                self._set_error(f"Backend failed to start for {model_id}")
                return False

        except Exception as e:
            logger.exception("Error during model switch")
            self._set_error(str(e))
            return False

    def stop_model(self):
        """Stop current model"""
        if self.status == ModelStatus.OFFLINE:
            return

        self._set_status(ModelStatus.STOPPING)
        try:
            pass
        except Exception as e:
            logger.error(f"Error stopping model: {e}")

        self.current_model_id = None
        self._set_status(ModelStatus.OFFLINE)

    def _set_status(self, status: ModelStatus):
        self.status = status
        self.state_changed_at = time.time()
        logger.info(f"ModelManager Status Changed: {status.value}")

    def _set_error(self, msg: str):
        self.last_error = msg
        self._set_status(ModelStatus.ERROR)
        logger.error(f"ModelManager Error: {msg}")
```

---

## 5. docker-compose.yml — possible extra_hosts

Ellie's docker-compose.yml should NOT be stripped (she keeps all services). But if
ai-proxy can't reach 10.20.10.10 from inside its container, add `extra_hosts` to
the ai-proxy service:

```yaml
  ai-proxy:
    ...
    extra_hosts:
      - "sparky:10.20.10.10"
```

Test first: `docker exec ai-proxy curl -s http://10.20.10.10:8000/health`
If that returns OK, no extra_hosts needed.

---

## 6. Deploy

```bash
cd ~/ai-stack
docker compose up -d --build ai-proxy
```

## 7. Verify

```bash
# Check ai-proxy can reach sparky's vLLM
docker exec ai-proxy curl -s http://10.20.10.10:8000/health

# Test remote model routing (Nemotron -> sparky)
curl -X POST http://localhost:8081/v1/chat/completions \
  -H "Authorization: Bearer $LLAMA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"Nemotron 3 Nano","messages":[{"role":"user","content":"Hello"}]}'

# Test local model routing (GLM -> local vllm-server)
curl -X POST http://localhost:8081/v1/chat/completions \
  -H "Authorization: Bearer $LLAMA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"GLM 4.7 Flash","messages":[{"role":"user","content":"Hello"}]}'

# Check model list includes host info
curl -H "x-api-key: $METRICS_API_KEY" http://localhost:8081/model/list | jq .
```
