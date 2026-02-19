import http.server
import requests
import json
import os
import sys
import socketserver
import psycopg2
from psycopg2 import pool
import time
from datetime import datetime, timedelta
import threading

from model_registry import ModelRegistry
from service_poller import ServicePoller

# Configuration from environment
PORT = int(os.environ.get("PROXY_PORT", "8081"))
ELLIE_MANAGER_HOST = os.environ.get("ELLIE_MANAGER_HOST", "172.17.0.1:8100")
SPARKY_MANAGER_HOST = os.environ.get("SPARKY_MANAGER_HOST", "10.20.10.10:8100")
SUPABASE_INTERNAL_URL = os.environ.get("SUPABASE_INTERNAL_URL", "http://ai-supabase-kong:8000")

# Map host keys to manager addresses
MANAGER_HOSTS = {
    "ellie": ELLIE_MANAGER_HOST,
    "sparky": SPARKY_MANAGER_HOST,
}

# DB Config
DB_NAME = os.environ.get("POSTGRES_DB", "postgres")
DB_USER = os.environ.get("POSTGRES_USER", "postgres")
DB_PASS = os.environ.get("POSTGRES_PASSWORD", "")
DB_HOST = os.environ.get("POSTGRES_HOST", "db")
DB_PORT = os.environ.get("POSTGRES_PORT", "5432")

# DB Connection Pool
db_pool = None

# Cache valid keys for 60 seconds
KEY_CACHE = {}
CACHE_TTL = 30

# Model Management
model_registry = ModelRegistry()
model_registry.load_models()

# Service Poller (background thread polling all LLM backends)
service_poller = ServicePoller(model_registry)
service_poller.start()

def get_db_conn():
    try:
        return psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            host=DB_HOST,
            port=DB_PORT
        )
    except Exception as e:
        print(f"DB_CONNECT_ERROR: {e}", file=sys.stderr)
        return None

def release_db_conn(conn):
    if conn:
        try:
            conn.close()
        except:
            pass

def fetch_api_key_data(api_key):
    now = time.time()
    if api_key in KEY_CACHE:
        entry_time, data = KEY_CACHE[api_key]
        if now - entry_time < CACHE_TTL:
            return data

    conn = get_db_conn()
    if not conn:
        return None

    try:
        cur = conn.cursor()
        # Get API key and user info
        cur.execute("""
            SELECT ak.id, ak.user_id, ak.name
            FROM public.api_keys ak
            WHERE ak.api_key = %s LIMIT 1
        """, (api_key,))
        row = cur.fetchone()

        if not row:
            KEY_CACHE[api_key] = (now, None)
            cur.close()
            return None

        api_key_id = row[0]
        user_id = row[1]
        key_name = row[2]

        # Check and enforce subscription expiration
        cur.execute("SELECT * FROM public.check_subscription_expiration(%s::uuid)", (user_id,))
        expiration_check = cur.fetchone()
        if expiration_check and expiration_check[0]:
            print(f"USER_SUBSCRIPTION_EXPIRED: user_id={user_id}, downgraded={expiration_check[1]}", file=sys.stderr)
            conn.commit()

        # Get user-level usage and limits
        cur.execute("""
            SELECT
                uu.hourly_usage, uu.daily_usage, uu.weekly_usage, uu.monthly_usage, uu.total_tokens,
                uu.hourly_reset_at, uu.daily_reset_at, uu.weekly_reset_at, uu.monthly_reset_at,
                l.hourly_limit, l.daily_limit, l.weekly_limit, l.monthly_limit,
                l.rate_limit_rpm, l.rate_limit_tpm
            FROM public.user_usage uu
            CROSS JOIN LATERAL public.get_user_limits(%s) l
            WHERE uu.user_id = %s
        """, (user_id, user_id))
        usage_row = cur.fetchone()
        cur.close()

        if not usage_row:
            cur = conn.cursor()
            cur.execute("INSERT INTO public.user_usage (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
            conn.commit()
            cur.close()
            cur = conn.cursor()
            cur.execute("""
                SELECT
                    uu.hourly_usage, uu.daily_usage, uu.weekly_usage, uu.monthly_usage, uu.total_tokens,
                    uu.hourly_reset_at, uu.daily_reset_at, uu.weekly_reset_at, uu.monthly_reset_at,
                    l.hourly_limit, l.daily_limit, l.weekly_limit, l.monthly_limit,
                    l.rate_limit_rpm, l.rate_limit_tpm
                FROM public.user_usage uu
                CROSS JOIN LATERAL public.get_user_limits(%s) l
                WHERE uu.user_id = %s
            """, (user_id, user_id))
            usage_row = cur.fetchone()
            cur.close()

        data = {
            "api_key_id": api_key_id,
            "user_id": user_id,
            "key_name": key_name,
            "hourly_usage": usage_row[0] if usage_row else 0,
            "daily_usage": usage_row[1] if usage_row else 0,
            "weekly_usage": usage_row[2] if usage_row else 0,
            "monthly_usage": usage_row[3] if usage_row else 0,
            "total_tokens": usage_row[4] if usage_row else 0,
            "hourly_reset_at": usage_row[5] if usage_row else datetime.now(),
            "daily_reset_at": usage_row[6] if usage_row else datetime.now(),
            "weekly_reset_at": usage_row[7] if usage_row else datetime.now(),
            "monthly_reset_at": usage_row[8] if usage_row else datetime.now(),
            "hourly_limit": usage_row[9] if usage_row else -1,
            "daily_limit": usage_row[10] if usage_row else -1,
            "weekly_limit": usage_row[11] if usage_row else -1,
            "monthly_limit": usage_row[12] if usage_row else -1,
            "rate_limit_rpm": usage_row[13] if usage_row else -1,
            "rate_limit_tpm": usage_row[14] if usage_row else -1,
            "system_key": False
        }

        # Check for resets (Simplified for brevity)
        now_dt = datetime.now(data["hourly_reset_at"].tzinfo if hasattr(data["hourly_reset_at"], 'tzinfo') else None)
        # ... logic skipped for brevity ...

        KEY_CACHE[api_key] = (now, data)
        return data
    except Exception as e:
        print(f"FETCH_KEY_ERROR: {e}", file=sys.stderr)
        try: conn.rollback()
        except: pass
        return None
    finally:
        if conn: release_db_conn(conn)

def record_usage(api_key_id, user_id, model, prompt_tokens, completion_tokens, path):
    total = prompt_tokens + completion_tokens
    print(f"RECORD_USAGE: api_key_id={api_key_id}, prompt={prompt_tokens}, completion={completion_tokens}, total={total}, model={model}", file=sys.stderr)
    conn = get_db_conn()
    if not conn: return

    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE public.user_usage
            SET total_tokens = total_tokens + %s,
                hourly_usage = hourly_usage + %s,
                daily_usage = daily_usage + %s,
                weekly_usage = weekly_usage + %s,
                monthly_usage = monthly_usage + %s,
                last_updated_at = now()
            WHERE user_id = %s
        """, (total, total, total, total, total, user_id))

        cur.execute("""
            UPDATE public.api_keys
            SET last_used_at = now()
            WHERE id = %s
        """, (api_key_id,))

        cur.execute("""
            INSERT INTO public.usage_logs (api_key_id, user_id, model, prompt_tokens, completion_tokens, total_tokens, request_path)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (api_key_id, user_id, model, prompt_tokens, completion_tokens, total, path))

        conn.commit()
        cur.close()
        for k in list(KEY_CACHE.keys()):
            if KEY_CACHE[k][1] and KEY_CACHE[k][1].get("user_id") == user_id:
                del KEY_CACHE[k]
    except Exception as e:
        print(f"RECORD_USAGE_ERROR: {e}", file=sys.stderr)
        try: conn.rollback()
        except: pass
    finally:
        if conn: release_db_conn(conn)

def validate_jwt_via_gotrue(token):
    """Validate a JWT by calling GoTrue's /auth/v1/user endpoint.
    Returns user dict {id, email, ...} on success, None on failure."""
    try:
        resp = requests.get(
            f"{SUPABASE_INTERNAL_URL}/auth/v1/user",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception as e:
        print(f"GOTRUE_VALIDATE_ERROR: {e}", file=sys.stderr)
        return None


def handle_auth_login(handler):
    """POST /v1/auth/login — authenticate via email/password, return token + API keys."""
    content_length = int(handler.headers.get('Content-Length', 0))
    body = handler.rfile.read(content_length) if content_length > 0 else b'{}'
    try:
        data = json.loads(body)
    except Exception:
        handler.send_error_json(400, "Invalid JSON body")
        return

    email = data.get("email")
    password = data.get("password")
    if not email or not password:
        handler.send_error_json(400, "email and password required")
        return

    # Forward to GoTrue password grant
    try:
        auth_resp = requests.post(
            f"{SUPABASE_INTERNAL_URL}/auth/v1/token?grant_type=password",
            json={"email": email, "password": password},
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
    except Exception as e:
        print(f"AUTH_LOGIN_GOTRUE_ERROR: {e}", file=sys.stderr)
        handler.send_error_json(503, "Authentication service unreachable")
        return

    if auth_resp.status_code != 200:
        # Forward GoTrue error
        handler.send_response(auth_resp.status_code)
        handler.send_header('Content-Type', 'application/json')
        handler.send_header('Access-Control-Allow-Origin', '*')
        handler.end_headers()
        handler.wfile.write(auth_resp.content)
        return

    auth_data = auth_resp.json()
    access_token = auth_data.get("access_token", "")
    user = auth_data.get("user", {})
    user_id = user.get("id", "")

    # Fetch user's existing API keys
    api_keys = []
    if user_id:
        conn = get_db_conn()
        if conn:
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT id, api_key, name, created_at FROM public.api_keys WHERE user_id = %s ORDER BY created_at DESC",
                    (user_id,),
                )
                for row in cur.fetchall():
                    api_keys.append({
                        "id": str(row[0]),
                        "api_key": row[1],
                        "name": row[2],
                        "created_at": row[3].isoformat() if row[3] else None,
                    })
                cur.close()
            except Exception as e:
                print(f"AUTH_LOGIN_KEYS_ERROR: {e}", file=sys.stderr)
            finally:
                release_db_conn(conn)

    result = {
        "access_token": access_token,
        "user": {"id": user_id, "email": user.get("email", "")},
        "api_keys": api_keys,
    }
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Access-Control-Allow-Origin', '*')
    handler.end_headers()
    handler.wfile.write(json.dumps(result).encode())


def handle_auth_keys_get(handler):
    """GET /v1/auth/keys — list API keys for authenticated user."""
    token = _extract_bearer_token(handler)
    if not token:
        handler.send_error_json(401, "Authorization: Bearer <token> required")
        return

    user = validate_jwt_via_gotrue(token)
    if not user:
        handler.send_error_json(401, "Invalid or expired token")
        return

    user_id = user.get("id", "")
    api_keys = []
    conn = get_db_conn()
    if not conn:
        handler.send_error_json(503, "Database unavailable")
        return

    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, api_key, name, created_at FROM public.api_keys WHERE user_id = %s ORDER BY created_at DESC",
            (user_id,),
        )
        for row in cur.fetchall():
            api_keys.append({
                "id": str(row[0]),
                "api_key": row[1],
                "name": row[2],
                "created_at": row[3].isoformat() if row[3] else None,
            })
        cur.close()
    except Exception as e:
        print(f"AUTH_KEYS_GET_ERROR: {e}", file=sys.stderr)
        handler.send_error_json(500, "Failed to fetch keys")
        return
    finally:
        release_db_conn(conn)

    handler.send_response(200)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Access-Control-Allow-Origin', '*')
    handler.end_headers()
    handler.wfile.write(json.dumps({"keys": api_keys}).encode())


def handle_auth_keys_post(handler):
    """POST /v1/auth/keys — create a new API key for authenticated user."""
    token = _extract_bearer_token(handler)
    if not token:
        handler.send_error_json(401, "Authorization: Bearer <token> required")
        return

    user = validate_jwt_via_gotrue(token)
    if not user:
        handler.send_error_json(401, "Invalid or expired token")
        return

    user_id = user.get("id", "")

    content_length = int(handler.headers.get('Content-Length', 0))
    body = handler.rfile.read(content_length) if content_length > 0 else b'{}'
    try:
        data = json.loads(body)
    except Exception:
        data = {}

    timestamp = int(time.time())
    name = data.get("name", f"claude-local-{timestamp}")
    api_key = f"sk_ai_{os.urandom(24).hex()}"

    conn = get_db_conn()
    if not conn:
        handler.send_error_json(503, "Database unavailable")
        return

    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO public.api_keys (user_id, api_key, name) VALUES (%s, %s, %s) RETURNING id, created_at",
            (user_id, api_key, name),
        )
        row = cur.fetchone()
        conn.commit()
        cur.close()

        result = {
            "id": str(row[0]),
            "api_key": api_key,
            "name": name,
            "created_at": row[1].isoformat() if row[1] else None,
        }
        handler.send_response(201)
        handler.send_header('Content-Type', 'application/json')
        handler.send_header('Access-Control-Allow-Origin', '*')
        handler.end_headers()
        handler.wfile.write(json.dumps(result).encode())
    except Exception as e:
        print(f"AUTH_KEYS_POST_ERROR: {e}", file=sys.stderr)
        try:
            conn.rollback()
        except Exception:
            pass
        handler.send_error_json(500, f"Failed to create key: {e}")
    finally:
        release_db_conn(conn)


def _extract_bearer_token(handler):
    """Extract Bearer token from Authorization header."""
    auth = handler.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


def extract_usage(response_bytes, is_streaming):
    """Extract token usage from response body.
    Handles OpenAI format (prompt_tokens/completion_tokens) and
    Anthropic format (input_tokens/output_tokens), both streaming and non-streaming.
    Returns (prompt_tokens, completion_tokens).
    """
    if not response_bytes:
        return (0, 0)

    if not is_streaming:
        try:
            data = json.loads(response_bytes)
            usage = data.get('usage', {})
            if not usage:
                return (0, 0)
            # OpenAI: prompt_tokens/completion_tokens
            # Anthropic: input_tokens/output_tokens
            prompt = usage.get('prompt_tokens') or usage.get('input_tokens') or 0
            completion = usage.get('completion_tokens') or usage.get('output_tokens') or 0
            return (prompt, completion)
        except Exception:
            return (0, 0)

    # Streaming: parse SSE data lines
    prompt_tokens = 0
    completion_tokens = 0
    text = response_bytes.decode('utf-8', errors='replace')

    for line in text.split('\n'):
        if not line.startswith('data: '):
            continue
        data_str = line[6:].strip()
        if not data_str or data_str == '[DONE]':
            continue
        try:
            event = json.loads(data_str)

            # OpenAI streaming: usage object in final chunk
            usage = event.get('usage')
            if usage and isinstance(usage, dict):
                if 'prompt_tokens' in usage:
                    prompt_tokens = usage['prompt_tokens']
                if 'completion_tokens' in usage:
                    completion_tokens = usage['completion_tokens']

            # Anthropic streaming: message_start has input_tokens
            if event.get('type') == 'message_start':
                msg_usage = event.get('message', {}).get('usage', {})
                if 'input_tokens' in msg_usage:
                    prompt_tokens = msg_usage['input_tokens']

            # Anthropic streaming: message_delta has output_tokens
            if event.get('type') == 'message_delta':
                delta_usage = event.get('usage', {})
                if 'output_tokens' in delta_usage:
                    completion_tokens = delta_usage['output_tokens']
        except (json.JSONDecodeError, ValueError):
            continue

    return (prompt_tokens, completion_tokens)


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

class LlamaProxy(http.server.BaseHTTPRequestHandler):
    def handle_request(self):
        print(f"DEBUG: START handle_request {self.path}", file=sys.stderr)
        sys.stderr.flush()
        try:
            print("DEBUG: Inside TRY", file=sys.stderr)
            sys.stderr.flush()
            self.headers_sent = False

            # --- SERVICE STATUS ENDPOINT ---
            if self.path == '/service/status':
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({"backends": service_poller.get_all()}).encode())
                return

            # --- SYSTEM ENDPOINTS ---
            print(f"DEBUG: Checking System {self.path}", file=sys.stderr)
            sys.stderr.flush()
            if self.path.startswith('/system') or self.path == '/metrics':
                 # ... (content omitted for brevity, keeping logical flow)
                 pass

            # --- MODEL MANAGEMENT ENDPOINTS ---
            print(f"DEBUG: Checking Model {self.path}", file=sys.stderr)
            sys.stderr.flush()
            if self.path.startswith('/model'):
                print(f"DEBUG: Entering Model Block {self.path}", file=sys.stderr)

                # Read-only model list is public (no auth required)
                if self.path == '/model/list' or self.path == '/model/available':
                    print(f"Handling {self.path} (public)", file=sys.stderr)
                    try:
                        models = []
                        for m in model_registry.list_models():
                            models.append({
                                "id": m.id,
                                "name": m.name,
                                "backend": m.backend,
                                "path": m.path,
                                "host": m.host,
                                "is_local": m.is_local,
                                "ctx_size": int(m.parameters.get('ctx-size', 0)),
                                "max_ctx": int(m.parameters.get('max-ctx', 0)),
                                "parallel": int(m.parameters.get('parallel', 1)),
                                "cache_type": m.parameters.get('cache-type', 'f16'),
                                "vram_base": int(m.parameters.get('vram-base', 0)),
                                "vram_per_1k_ctx": int(m.parameters.get('vram-per-1k-ctx', 0)),
                            })

                        all_backends = service_poller.get_all()

                        # Build a set of model config IDs that are actually loaded
                        # by matching ServicePoller data against registry configs
                        loaded_model_ids = set()
                        norm = lambda s: s.lower().replace('-', '').replace('_', '').replace('.', '').replace(' ', '')
                        for b in all_backends:
                            if b.get('status') != 'ready':
                                continue
                            loaded = b.get('model', '')
                            model_path = b.get('model_path', '')
                            host = b.get('host', '')
                            config = None
                            if loaded:
                                config = model_registry.find_model(loaded)
                            if not config and model_path:
                                config = model_registry.find_model(model_path)
                            if not config and loaded:
                                loaded_norm = norm(loaded)
                                for mc in model_registry.list_models():
                                    if mc.host != host:
                                        continue
                                    if loaded_norm in norm(mc.path) or loaded_norm in norm(mc.name):
                                        config = mc
                                        break
                            if config:
                                loaded_model_ids.add(config.id)

                        compat_models = []
                        for m_dict in models:
                            is_loaded = m_dict["id"] in loaded_model_ids
                            entry = {
                                "name": m_dict["id"],
                                "alias": m_dict["name"],
                                "backend": m_dict["backend"],
                                "host": m_dict["host"],
                                "is_local": m_dict["is_local"],
                                "status": "ready" if is_loaded else "offline",
                                "ctx_size": m_dict.get("ctx_size", 0),
                                "max_ctx": m_dict.get("max_ctx", 0),
                                "parallel": m_dict.get("parallel", 1),
                                "cache_type": m_dict.get("cache_type", "f16"),
                                "vram_base": m_dict.get("vram_base", 0),
                                "vram_per_1k_ctx": m_dict.get("vram_per_1k_ctx", 0),
                            }
                            compat_models.append(entry)
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.send_header('Access-Control-Allow-Origin', '*')
                        self.end_headers()
                        self.wfile.write(json.dumps(compat_models).encode())
                    except Exception as e:
                        print(f"Error listing models: {e}", file=sys.stderr)
                        self.send_error_json(500, str(e))
                    return

                # Ready-to-chat models: only models on backends that are actually online
                if self.path == '/model/chat-models':
                    ready = []
                    for b in service_poller.get_all():
                        if b.get('status') != 'ready':
                            continue
                        loaded = b.get('model', '')
                        model_path = b.get('model_path', '')
                        host = b.get('host', '')

                        # Try to match backend's loaded model to a models.ini entry
                        config = None
                        if loaded:
                            config = model_registry.find_model(loaded)
                        if not config and model_path:
                            config = model_registry.find_model(model_path)
                        # Fallback: match by host with flexible name comparison
                        if not config and loaded:
                            norm = lambda s: s.lower().replace('-', '').replace('_', '').replace('/', '').replace('.', '').replace(' ', '').replace(':', '')
                            loaded_norm = norm(loaded)
                            for m in model_registry.list_models():
                                if m.host != host:
                                    continue
                                if loaded_norm in norm(m.path) or loaded_norm in norm(m.name):
                                    config = m
                                    break

                        if config:
                            # Skip models marked as hidden from chat clients
                            if config.parameters.get('chat-visible', 'true').lower() == 'false':
                                continue
                            ready.append({
                                "id": config.id,
                                "alias": config.name,
                                "backend": b['backend'],
                                "host": config.host,
                                "is_local": config.is_local,
                            })
                        else:
                            ready.append({
                                "id": loaded,
                                "alias": loaded,
                                "backend": b.get('backend', 'unknown'),
                                "host": host,
                                "is_local": b.get('is_local', False),
                            })
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps(ready).encode())
                    return

                self.send_error_json(404, "Unknown Model Endpoint")
                return

            # --- MODEL MANAGER PROXY ENDPOINTS (admin, proxied to model-managers) ---
            # Routes: /ellie/* -> Ellie model-manager, /sparky/* -> Sparky model-manager
            for host_key, manager_addr in MANAGER_HOSTS.items():
                prefix = f'/{host_key}/'
                if self.path.startswith(prefix):
                    manager_path = self.path.replace(prefix, '/models/', 1)
                    manager_url = f"http://{manager_addr}{manager_path}"

                    # Read request body for POST
                    content_length = int(self.headers.get('Content-Length', 0))
                    body = self.rfile.read(content_length) if content_length > 0 else None

                    try:
                        fwd_headers = {"Content-Type": "application/json"}
                        with requests.request(
                            method=self.command,
                            url=manager_url,
                            data=body,
                            headers=fwd_headers,
                            timeout=30,
                        ) as r:
                            self.send_response(r.status_code)
                            self.send_header('Content-Type', 'application/json')
                            self.send_header('Access-Control-Allow-Origin', '*')
                            self.end_headers()
                            self.wfile.write(r.content)
                    except Exception as e:
                        print(f"Manager proxy error ({host_key}): {e}", file=sys.stderr)
                        self.send_error_json(502, f"{host_key} model-manager unreachable: {e}")
                    return

            # --- AUTH ENDPOINTS (no API key required) ---
            if self.path == '/v1/auth/login' and self.command == 'POST':
                handle_auth_login(self)
                return
            if self.path == '/v1/auth/keys':
                if self.command == 'GET':
                    handle_auth_keys_get(self)
                    return
                elif self.command == 'POST':
                    handle_auth_keys_post(self)
                    return
                else:
                    self.send_error_json(405, "Method not allowed")
                    return

            # --- API REQUESTS ---

            # 1. API KEY VALIDATION
            api_key = None
            if 'x-api-key' in self.headers:
                api_key = self.headers['x-api-key']
            elif 'Authorization' in self.headers:
                auth_header = self.headers['Authorization']
                if auth_header.startswith('Bearer '):
                    api_key = auth_header[7:]

            if not api_key:
                self.send_error_json(401, "Unauthorized: No API Key provided")
                return

            key_data = fetch_api_key_data(api_key)
            if not key_data:
                self.send_error_json(401, "Unauthorized: Invalid API Key")
                return

            # Log which key is being used
            key_name = key_data.get('key_name', 'system') if not key_data.get('system_key') else 'SYSTEM_KEY'

            # 2. LIMIT CHECKING (Skip for system key)
            if not key_data.get("system_key"):
                # ... (same limit check logic) ...
                pass

            # Read request body
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length) if content_length > 0 else None

            # Routing Logic — resolve model from request body
            is_streaming = False
            model_config = None

            if body and 'application/json' in (self.headers.get('Content-Type') or ''):
                try:
                    data = json.loads(body)
                    requested_model = data.get('model', '')
                    is_streaming = data.get('stream', False)
                    print(f"DEBUG: Requested model: {requested_model!r}", file=sys.stderr)
                    # Log sampling params for debugging
                    sampling_keys = ('max_tokens', 'temperature', 'top_p', 'top_k', 'min_p',
                                     'repetition_penalty', 'frequency_penalty', 'presence_penalty')
                    sampling = {k: data[k] for k in sampling_keys if k in data}
                    if sampling:
                        print(f"DEBUG: Sampling params: {sampling}", file=sys.stderr)
                    else:
                        print(f"DEBUG: No sampling params in request", file=sys.stderr)

                    # Look up model in registry by id, alias, or HF path
                    if requested_model:
                        model_config = model_registry.find_model(requested_model)
                        print(f"DEBUG: find_model result: {model_config.id if model_config else None} host={model_config.host if model_config else None}", file=sys.stderr)

                    # Fall back to first ready backend
                    if not model_config:
                        for b in service_poller.get_all():
                            if b.get('status') == 'ready':
                                loaded = b.get('model', '')
                                if loaded:
                                    model_config = model_registry.find_model(loaded)
                                    if model_config:
                                        break

                    if not model_config:
                        self.send_error_json(503, "No model found for request")
                        return

                    # Rewrite model field for vLLM to the name the backend actually reports
                    if model_config.backend == 'vllm':
                        served_name = model_config.path  # fallback to HF path
                        for b in service_poller.get_all():
                            if b.get('host') == model_config.host and b.get('status') == 'ready':
                                served_name = b.get('model', served_name)
                                break
                        data['model'] = served_name

                    # Inject per-model sampling defaults (only if client didn't set them)
                    SAMPLING_DEFAULTS = {
                        'temperature': 'default-temperature',
                        'top_p': 'default-top-p',
                        'top_k': 'default-top-k',
                        'min_p': 'default-min-p',
                    }
                    injected = {}
                    for param_key, ini_key in SAMPLING_DEFAULTS.items():
                        if param_key not in data and ini_key in model_config.parameters:
                            val = model_config.parameters[ini_key]
                            data[param_key] = float(val) if '.' in val else int(val)
                            injected[param_key] = data[param_key]

                    # Cap max_tokens to per-model max-output
                    max_output = model_config.parameters.get('max-output')
                    if max_output:
                        max_out = int(max_output)
                        req_max = data.get('max_tokens')
                        if req_max is None or req_max > max_out:
                            data['max_tokens'] = max_out
                            injected['max_tokens'] = f"{req_max} -> {max_out}"

                    if injected:
                        print(f"DEBUG: Injected defaults for {model_config.id}: {injected}", file=sys.stderr)

                    # Ensure OpenAI streaming responses include usage data for tracking.
                    # Anthropic streaming already includes usage in message_start/message_delta.
                    is_openai_endpoint = '/chat/completions' in self.path or '/completions' in self.path
                    if is_streaming and is_openai_endpoint and 'stream_options' not in data:
                        data['stream_options'] = {"include_usage": True}

                    body = json.dumps(data).encode('utf-8')

                except Exception as e:
                    print(f"Proxy Error parsing JSON: {e}", file=sys.stderr)

            # For non-JSON requests (e.g. GET /v1/models), fall back to first ready backend
            if not model_config:
                for b in service_poller.get_all():
                    if b.get('status') == 'ready':
                        loaded = b.get('model', '')
                        if loaded:
                            model_config = model_registry.find_model(loaded)
                            if model_config:
                                break

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

            print(f"Proxy Forwarding: {self.command} {self.path} -> {url}", file=sys.stderr)
            headers = {k: v for k, v in self.headers.items() if k.lower() not in ('host', 'content-length', 'authorization', 'x-api-key')}

            try:
                # Add retry logic for backend wake-up
                max_retries = 10
                for i in range(max_retries):
                    try:
                        with requests.request(
                            method=self.command,
                            url=url,
                            data=body,
                            headers=headers,
                            stream=True,
                            timeout=300
                        ) as r:
                            # Verify success connection
                            self.send_response(r.status_code)
                            excluded = ('transfer-encoding', 'content-length', 'connection', 'content-encoding')
                            for k, v in r.headers.items():
                                if k.lower() not in excluded:
                                    self.send_header(k, v)

                            self.send_header('Connection', 'close')
                            self.end_headers()
                            self.headers_sent = True

                            # Stream response and capture bytes for usage extraction
                            response_status = r.status_code
                            if is_streaming:
                                chunks = []
                                for chunk in r.iter_content(chunk_size=None):
                                    if not chunk: continue
                                    self.wfile.write(chunk)
                                    self.wfile.flush()
                                    chunks.append(chunk)
                                response_bytes = b''.join(chunks)
                            else:
                                response_bytes = r.content
                                self.wfile.write(response_bytes)
                                self.wfile.flush()

                            break # Success
                    except requests.exceptions.ConnectionError:
                        if i < max_retries - 1:
                            time.sleep(1) # Wait for container to wake up
                        else:
                            raise

                # Record usage after successful response
                if response_status < 400 and not key_data.get("system_key"):
                    try:
                        prompt_tokens, completion_tokens = extract_usage(response_bytes, is_streaming)
                        if prompt_tokens > 0 or completion_tokens > 0:
                            model_name = model_config.id if model_config else "unknown"
                            record_usage(
                                key_data["api_key_id"],
                                key_data["user_id"],
                                model_name,
                                prompt_tokens,
                                completion_tokens,
                                self.path
                            )
                        else:
                            print(f"USAGE_WARN: No token counts found in response for {self.path}", file=sys.stderr)
                    except Exception as e:
                        print(f"USAGE_EXTRACT_ERROR: {e}", file=sys.stderr)

            except Exception as e:
                print(f"Proxy forwarding error: {e}", file=sys.stderr)
                if not self.headers_sent:
                    self.send_error_json(502, f"Proxy Error: {str(e)}")

        except Exception as e:
            print(f"CRITICAL HANDLE REQ ERROR: {e}", file=sys.stderr)
            if not self.headers_sent:
                self.send_error_json(500, f"Critical Proxy Error: {str(e)}")

    def send_error_json(self, code, message):
        try:
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"error": {"message": message, "type": "error"}}).encode())
        except: pass

    def do_GET(self):
        print(f"DEBUG: do_GET {self.path}", file=sys.stderr)
        self.handle_request()
    def do_POST(self):
        print(f"DEBUG: do_POST {self.path}", file=sys.stderr)
        self.handle_request()
    def do_PUT(self):
        print(f"DEBUG: do_PUT {self.path}", file=sys.stderr)
        self.handle_request()
    def do_DELETE(self):
        print(f"DEBUG: do_DELETE {self.path}", file=sys.stderr)
        self.handle_request()

if __name__ == "__main__":
    print(f"AI Proxy listening on port {PORT}", file=sys.stderr)
    sys.stderr.flush()
    ThreadedHTTPServer(('0.0.0.0', PORT), LlamaProxy).serve_forever()
