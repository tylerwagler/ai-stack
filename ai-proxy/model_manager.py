import logging
import time
from enum import Enum
from typing import Optional, Dict, Any
from model_registry import ModelRegistry, ModelConfig

logger = logging.getLogger(__name__)

class ModelStatus(Enum):
    OFFLINE = "offline"
    STARTING = "starting"
    LOADING = "loading"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"

class ModelManager:
    def __init__(self, registry: ModelRegistry):
        self.registry = registry

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

        # 3. Update routing state (actual backend lifecycle managed by docker compose)
        self.current_model_id = model_id
        self._set_status(ModelStatus.RUNNING)
        self.target_model_id = None
        return True

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
