import configparser
import os
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class ModelConfig:
    id: str
    name: str  # alias
    path: str
    backend: str  # 'llama' | 'vllm'
    parameters: Dict[str, str] = field(default_factory=dict)
    
    # Computed property for safe filesystem path or ID
    @property
    def safe_id(self):
        return self.id.replace(" ", "_").lower()

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
                    if key not in ['model', 'alias', 'backend']:
                        params[key] = value

                model_config = ModelConfig(
                    id=model_id,
                    name=alias,
                    path=path,
                    backend=backend,
                    parameters=params
                )
                
                new_models[model_id] = model_config
                logger.info(f"Loaded model config: {model_id} -> {backend} (Alias: {alias})")

            self.models = new_models
            return self.models

        except Exception as e:
            logger.error(f"Error parse models.ini: {e}")
            return {}

    def get_model(self, model_id: str) -> Optional[ModelConfig]:
        if not self.models:
            self.load_models()
        return self.models.get(model_id)

    def list_models(self) -> List[ModelConfig]:
        if not self.models:
            self.load_models()
        return list(self.models.values())
