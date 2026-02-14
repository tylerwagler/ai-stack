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
        
        # State
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
                "backend": current_config.backend
            } if current_config else None,
            "last_error": self.last_error,
            "uptime_seconds": time.time() - self.state_changed_at if self.status == ModelStatus.RUNNING else 0
        }

    def list_models(self):
        """Return list of available models with their backend config"""
        return [
            {
                "id": m.id,
                "name": m.name,
                "backend": m.backend,
                "path": m.path,
                "is_active": m.id == self.current_model_id
            }
            for m in self.registry.list_models()
        ]

    def switch_model(self, model_id: str) -> bool:
        """Initiate valid state transition to switch model"""
        logger.info(f"Requested switch to model: {model_id}")
        
        # 1. Validate Model
        config = self.registry.get_model(model_id)
        if not config:
            self._set_error(f"Model {model_id} not found in registry")
            return False

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
            # This is synchronous for now, but could be async
            success = self.backend_manager.switch_backend(config.backend, config.path)
            
            if success:
                self.current_model_id = model_id
                self._set_status(ModelStatus.RUNNING) # TODO: Ideally wait for /health?
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
            # We don't have a stop_backend method yet, but we can just use the backend manager
            # to stop everything? Or keep the backend running but "unload"?
            # For now, let's assume we just mark it as offline.
            # Real implementation might call `backend_manager.stop_all()`
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
