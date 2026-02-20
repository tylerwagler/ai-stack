"""
ai-proxy — Lightweight model routing proxy.

Routes /v1/* requests to the correct backend based on the model field
in the request body. Passes through without any format translation.
Both Anthropic (/v1/messages) and OpenAI (/v1/chat/completions) formats
are forwarded as-is.

Auth is handled by the gateway (nginx auth_request against Open WebUI).
This proxy trusts all incoming requests.
"""

import http.server
import json
import os
import sys
import socketserver
import requests
import time

PORT = int(os.environ.get("PROXY_PORT", "8081"))
CONFIG_PATH = os.environ.get("MODELS_CONFIG", "/app/models.json")

# Model name → (backend_url, backend_model_name)
MODELS = {}
# Alias → canonical model name (e.g. "claude-opus-4-6" → "qwen3 coder next")
ALIASES = {}
# Default backend URL when model can't be resolved
DEFAULT_BACKEND = None
# Default backend model name for the default backend
DEFAULT_BACKEND_MODEL = None
# Params to strip from request body before forwarding
DROP_PARAMS = set()


def load_config():
    global MODELS, ALIASES, DEFAULT_BACKEND, DEFAULT_BACKEND_MODEL, DROP_PARAMS

    with open(CONFIG_PATH) as f:
        config = json.load(f)

    MODELS = {}
    for model in config.get("models", []):
        url = model["url"]
        backend_model = model.get("backend_model", model["names"][0])
        for name in model["names"]:
            MODELS[name.lower()] = (url, backend_model)

    ALIASES = {k.lower(): v.lower() for k, v in config.get("aliases", {}).items()}
    DEFAULT_BACKEND = config.get("default_backend", "")
    DEFAULT_BACKEND_MODEL = config.get("default_backend_model")
    DROP_PARAMS = set(config.get("drop_params", []))

    print(f"Loaded {len(MODELS)} model names, {len(ALIASES)} aliases", file=sys.stderr)
    print(f"Default backend: {DEFAULT_BACKEND}", file=sys.stderr)
    if DROP_PARAMS:
        print(f"Dropping params: {DROP_PARAMS}", file=sys.stderr)


def resolve_backend(model_name):
    """Resolve a model name (or alias) to (backend_url, backend_model_name)."""
    name = model_name.lower()
    # Check aliases first
    if name in ALIASES:
        name = ALIASES[name]
    if name in MODELS:
        return MODELS[name]
    if DEFAULT_BACKEND:
        return (DEFAULT_BACKEND, DEFAULT_BACKEND_MODEL or model_name)
    return None


def build_models_response():
    """Build an OpenAI-compatible /v1/models response from config."""
    seen = set()
    data = []
    for model in json.load(open(CONFIG_PATH)).get("models", []):
        display_name = model["names"][0]
        if display_name not in seen:
            seen.add(display_name)
            data.append({
                "id": display_name,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "ai-stack",
            })
    return {"object": "list", "data": data}


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        """Override to log to stderr with timestamp."""
        print(f"{self.log_date_time_string()} {format % args}", file=sys.stderr)

    def handle_request(self):
        try:
            # Handle /v1/models locally
            if self.path.rstrip("/") == "/v1/models" and self.command == "GET":
                body = json.dumps(build_models_response()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            # Read request body
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else None

            # Extract model from body and resolve backend
            backend_url = None
            if body:
                try:
                    data = json.loads(body)
                    model = data.get("model", "")
                    resolved = None
                    if model:
                        resolved = resolve_backend(model)

                    if resolved:
                        backend_url, backend_model = resolved
                        # Rewrite model field to what the backend expects
                        if backend_model and backend_model != model:
                            data["model"] = backend_model
                            print(f"Model: {model!r} -> {backend_url} (rewrite to {backend_model!r})", file=sys.stderr)
                        else:
                            print(f"Model: {model!r} -> {backend_url}", file=sys.stderr)

                    # Strip unsupported params
                    if DROP_PARAMS:
                        for param in DROP_PARAMS:
                            data.pop(param, None)

                    body = json.dumps(data).encode()

                except json.JSONDecodeError:
                    pass

            if not backend_url:
                backend_url = DEFAULT_BACKEND

            if not backend_url:
                self.send_error_json(503, "No backend available for model")
                return

            # Forward request to backend (same path)
            url = f"{backend_url}{self.path}"
            headers = {
                k: v for k, v in self.headers.items()
                if k.lower() not in ("host", "content-length", "authorization", "x-api-key")
            }

            print(f"Forwarding: {self.command} {self.path} -> {url}", file=sys.stderr)

            with requests.request(
                method=self.command,
                url=url,
                data=body,
                headers=headers,
                stream=True,
                timeout=600,
            ) as r:
                self.send_response(r.status_code)
                excluded = ("transfer-encoding", "content-length", "connection", "content-encoding")
                for k, v in r.headers.items():
                    if k.lower() not in excluded:
                        self.send_header(k, v)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Connection", "close")
                self.end_headers()

                for chunk in r.iter_content(chunk_size=None):
                    if chunk:
                        self.wfile.write(chunk)
                        self.wfile.flush()

        except (BrokenPipeError, ConnectionResetError):
            pass  # Client disconnected
        except Exception as e:
            print(f"Proxy error: {e}", file=sys.stderr)
            try:
                self.send_error_json(502, f"Backend error: {e}")
            except Exception:
                pass

    def send_error_json(self, code, message):
        body = json.dumps({"error": {"message": message, "type": "error"}}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()


if __name__ == "__main__":
    load_config()
    print(f"AI Proxy listening on port {PORT}", file=sys.stderr)
    sys.stderr.flush()
    ThreadedHTTPServer(("0.0.0.0", PORT), ProxyHandler).serve_forever()
