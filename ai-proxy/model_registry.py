import configparser
import os
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Default host for models without an explicit host field in models.ini
DEFAULT_HOST = os.environ.get("DEFAULT_MODEL_HOST", "llama-server:8082")

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
        """True if this model runs on a local container (llama-server)."""
        h = self.host.split(":")[0]
        return h in ("llama-server", "localhost", "127.0.0.1")

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
                host = config[section].get('host', DEFAULT_HOST)

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
