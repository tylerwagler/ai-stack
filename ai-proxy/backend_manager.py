
import docker
import logging
import time
import os

logger = logging.getLogger(__name__)

class BackendManager:
    def __init__(self):
        self.client = docker.from_env()
        self.current_backend = None
        self.containers = {
            "llama": "llama-server",
            "vllm": "vllm-server"
        }
        self.backend_ports = {
            "llama": 8082,
            "vllm": 8000
        }

    def get_container(self, name):
        try:
            return self.client.containers.get(name)
        except docker.errors.NotFound:
            return None

    def switch_backend(self, target_backend, model_config=None):
        if target_backend not in self.containers:
            logger.error(f"Unknown backend: {target_backend}")
            return False

        logger.info(f"Switching to backend: {target_backend} for model {model_config.id if model_config else 'unknown'}")

        # Stop other backends
        for backend, container_name in self.containers.items():
            if backend != target_backend:
                self.stop_backend(backend)

        # Start target backend
        container_name = self.containers[target_backend]
        container = self.get_container(container_name)
        
        if not container:
            logger.error(f"Container {container_name} not found!")
            return False

        if target_backend == 'vllm' and model_config:
            # Dynamic vLLM switching logic
            # Check if current args match needed args
            current_model_arg = f"--model {model_config.path}"
            needs_recreate = False
            
            # Inspect container command/args
            # Note: container.attrs['Config']['Cmd'] gives the command
            try:
                cmd = container.attrs.get('Config', {}).get('Cmd', [])
                cmd_str = " ".join(cmd) if cmd else ""
                
                # Simple check: if the model path isn't in the command, we need to switch
                # This assumes the command structure is stable
                if model_config.path not in cmd_str:
                    logger.info(f"vLLM model mismatch. Current cmd: {cmd_str}. Target: {model_config.path}")
                    needs_recreate = True
            except Exception as e:
                logger.warning(f"Could not inspect vLLM container config: {e}")
                needs_recreate = True

            if needs_recreate:
                logger.info(f"Recreating vLLM container for model {model_config.id}...")
                try:
                    # 1. Stop and remove existing container
                    container.stop()
                    container.remove()
                    
                    # 2. Prepare new run command
                    # base_command from docker-compose is hard to retrieve exactly without parsing compose file
                    # We will construct a standard command based on our knowledge of vLLM args
                    # TODO: Ideally we read this from a template or env vars
                    # For now, we replicate the docker-compose command structure
                    params = model_config.parameters
                    
                    new_command = [
                        "--model", model_config.path,
                        "--tensor-parallel-size", params.get("tensor_parallel_size", "2"),
                        "--attention-backend", "flashinfer",
                        "--gpu-memory-utilization", "0.8",
                        "--max-model-len", params.get("context_size", "8192"),
                        "--port", "8000",
                        "--trust-remote-code"
                    ]
                    
                    # 3. Run new container
                    # We need to preserve volumes, ports, device requests from original if possible
                    # Or just run knowing our environment
                    
                    # Re-run using docker client
                    # We need to map 8000:8000, mount volumes, set GPU, set env vars
                    # This is complex to do "right" purely from code without dependencies on compose
                    # SHORTCUT: Only support switching if we can just "restart" with env vars? 
                    # vLLM doesn't support env var for model name cleanly.
                    
                    # BETTER APPROACH FOR NOW:
                    # Just warn that we can't dynamic switch easily without `docker compose up` override?
                    # OR: We assume the user creates separate services for separate models? No that defeats the point.
                    
                    # Let's try the `run` command with specific args matching our infrastructure
                    
                    self.client.containers.run(
                        image="vllm/vllm-openai:latest",
                        command=new_command,
                        name=container_name,
                        detach=True,
                        ports={'8000/tcp': 8000},
                        volumes={'llama_cache': {'bind': '/root/.cache/huggingface', 'mode': 'rw'}},
                        environment={"HUGGING_FACE_HUB_TOKEN": os.environ.get("HUGGING_FACE_HUB_TOKEN", "")},
                        device_requests=[
                            docker.types.DeviceRequest(count=-1, capabilities=[['gpu']])
                        ],
                        runtime="nvidia",
                        ipc_mode="host",
                        network="ai-stack-net"
                    )
                    
                    # Refresh container ref
                    container = self.get_container(container_name)
                    logger.info("vLLM container recreated successfully.")
                    
                except Exception as e:
                    logger.error(f"Failed to recreate vLLM container: {e}")
                    return False

        # Ensure running
        container = self.get_container(container_name) # Refetch in case we recreated
        if container.status != "running":
            logger.info(f"Starting {container_name}...")
            container.start()
            
        self.current_backend = target_backend
        return True

    def stop_backend(self, backend):
        """Stop a specific backend container"""
        name = self.containers.get(backend)
        if not name: return
        
        container = self.get_container(name)
        if container and container.status == "running":
            logger.info(f"Stopping {name}...")
            container.stop()

    def get_backend_url(self, backend_name=None):
        if not backend_name:
            backend_name = self.current_backend
        
        if not backend_name:
            return None
            
        # In docker network, we use container name. 
        # But from proxy container, we can access them by service name usually.
        # Container name = Service name in our compose (mostly).
        host = self.containers.get(backend_name)
        port = self.backend_ports.get(backend_name)
        if host and port:
            return f"http://{host}:{port}"
        return None

    def get_status(self):
        status = {}
        for backend, name in self.containers.items():
            c = self.get_container(name)
            status[backend] = c.status if c else "not_found"
        return status
