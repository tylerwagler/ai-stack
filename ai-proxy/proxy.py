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
from model_manager import ModelManager

# Configuration from environment
PORT = int(os.environ.get("PROXY_PORT", "8081"))
LLAMA_API_KEY = os.environ.get("LLAMA_API_KEY", "")

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
model_manager = ModelManager(model_registry)

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
    # System key check
    if api_key == LLAMA_API_KEY:
        return {"system_key": True}

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

                # 1. API Key Check for System Endpoints
                api_key = None
                if 'x-api-key' in self.headers:
                    api_key = self.headers['x-api-key']
                elif 'Authorization' in self.headers:
                    auth_header = self.headers['Authorization']
                    if auth_header.startswith('Bearer '):
                        api_key = auth_header[7:]
                
                # Check query param for key (sometimes used in simple GETs)
                if not api_key and '?' in self.path:
                    from urllib.parse import parse_qs, urlparse
                    query = parse_qs(urlparse(self.path).query)
                    if 'key' in query:
                         api_key = query['key'][0]

                # Check env for METRICS_API_KEY override
                METRICS_KEY = os.environ.get("METRICS_API_KEY", LLAMA_API_KEY)
                
                if api_key != LLAMA_API_KEY and api_key != METRICS_KEY:
                     # Check if it's a valid user key with admin?
                     # Fetch key data
                     key_data = fetch_api_key_data(api_key) if api_key else None
                     if not key_data or not key_data.get('system_key'):
                         self.send_error_json(401, "Unauthorized: System Access Required")
                         return

                if self.path == '/system/status' or self.path == '/metrics':
                    try:
                        model_state = model_manager.get_status()
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.send_header('Access-Control-Allow-Origin', '*')
                        self.end_headers()
                        self.wfile.write(json.dumps(model_state).encode())
                    except Exception as e:
                        self.send_error_json(500, f"System Status Error: {str(e)}")
                    return

                # --- MODEL MANAGEMENT ENDPOINTS ---
                if self.path.startswith('/model'):
                    print(f"DEBUG: Entering Model Block {self.path}", file=sys.stderr)
                    # Auth Check (Same as System)
                    api_key = None
                    if 'x-api-key' in self.headers:
                        api_key = self.headers['x-api-key']
                    elif 'Authorization' in self.headers:
                        auth_header = self.headers['Authorization']
                        if auth_header.startswith('Bearer '):
                            api_key = auth_header[7:]

                    # Allow query param for key
                    if not api_key and '?' in self.path:
                        from urllib.parse import parse_qs, urlparse
                        query = parse_qs(urlparse(self.path).query)
                        if 'key' in query:
                             api_key = query['key'][0]
                    
                    METRICS_KEY = os.environ.get("METRICS_API_KEY", LLAMA_API_KEY)
                    print(f"DEBUG: Auth Check. Path={self.path} API_KEY_Present={bool(api_key)}", file=sys.stderr)
                    
                    valid_auth = False
                    if api_key == LLAMA_API_KEY or api_key == METRICS_KEY:
                        valid_auth = True
                        print("DEBUG: Auth Success (System/Metrics Key)", file=sys.stderr)
                    else:
                         key_data = fetch_api_key_data(api_key) if api_key else None
                         if key_data and key_data.get('system_key'):
                             valid_auth = True
                             print("DEBUG: Auth Success (DB System Key)", file=sys.stderr)
                         else:
                             print(f"DEBUG: Reuse DB Key Failed. KeyData={key_data}", file=sys.stderr)
                    
                    print(f"DEBUG: Auth Result: {valid_auth}", file=sys.stderr)
                    
                    if not valid_auth:
                         print("DEBUG: Auth Failed", file=sys.stderr)
                         self.send_error_json(401, "Unauthorized: Admin Access Required")
                         return

                    if self.path == '/model/status':
                        state = model_manager.get_status()
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.send_header('Access-Control-Allow-Origin', '*')
                        self.end_headers()
                        self.wfile.write(json.dumps(state).encode())
                        return

                    if self.path == '/model/list' or self.path == '/model/available':
                        print(f"Handling {self.path}", file=sys.stderr)
                        try:
                            models = model_manager.list_models()
                            print(f"Models listed: {len(models)}", file=sys.stderr)
                            # Compat for frontend calling /model/available
                            # It expects: name, alias, status
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
                            
                            print("Sending response...", file=sys.stderr)
                            self.send_response(200)
                            self.send_header('Content-Type', 'application/json')
                            self.send_header('Access-Control-Allow-Origin', '*')
                            self.end_headers()
                            self.wfile.write(json.dumps(compat_models).encode())
                            print("Response sent.", file=sys.stderr)
                        except Exception as e:
                            print(f"Error in model list: {e}", file=sys.stderr)
                            raise e
                        return

                    if self.path == '/model/switch':
                        content_length = int(self.headers.get('Content-Length', 0))
                        body = self.rfile.read(content_length)
                        try:
                            data = json.loads(body)
                            target_model = data.get('model')
                            if not target_model:
                                 self.send_error_json(400, "Missing model name/id")
                                 return
                            
                            success = model_manager.switch_model(target_model)
                            
                            if success:
                                self.send_response(200)
                                self.send_header('Content-Type', 'application/json')
                                self.send_header('Access-Control-Allow-Origin', '*')
                                self.end_headers()
                                self.wfile.write(json.dumps({"status": "switching_initiated"}).encode())
                            else:
                                self.send_error_json(500, f"Failed to switch to {target_model}: {model_manager.last_error}")
                                
                        except Exception as e:
                            print(f"Switch error: {e}", file=sys.stderr)
                            self.send_error_json(500, str(e))
                        return
                    
                    # Legacy support /model/current
                    if self.path == '/model/current':
                        state = model_manager.get_status()
                        current = state.get("current_model")
                        data = {
                            "name": current.get("id") if current else "none",
                            "alias": current.get("name") if current else "none",
                            "status": state.get("status"),
                            "backend": current.get("backend") if current else "none"
                        }
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.send_header('Access-Control-Allow-Origin', '*')
                        self.end_headers()
                        self.wfile.write(json.dumps(data).encode())
                        return

                    self.send_error_json(404, "Unknown Model Endpoint")
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

                print(f"Proxy Forwarding: {self.command} {self.path} -> {url}", file=sys.stderr)
                headers = {k: v for k, v in self.headers.items() if k.lower() not in ('host', 'content-length', 'authorization', 'x-api-key')}
                headers['Authorization'] = f"Bearer {LLAMA_API_KEY}"
                
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
                                
                                # Stream processing ...
                                # (Basic pass-through for now to save space, but keeping usage tracking logic if possible)
                                
                                prompt_tokens = 0
                                completion_tokens = 0
                                
                                if is_streaming:
                                    for chunk in r.iter_content(chunk_size=None):
                                        if not chunk: continue
                                        self.wfile.write(chunk)
                                        self.wfile.flush()
                                        # Usage extraction logic would go here
                                else:
                                    self.wfile.write(r.content)
                                    self.wfile.flush()
                                    # Usage logic here
                                
                                # if not key_data.get("system_key"): ... record usage ...
                                break # Success
                        except requests.exceptions.ConnectionError:
                            if i < max_retries - 1:
                                time.sleep(1) # Wait for container to wake up
                            else:
                                raise

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
    print(f"Llama Proxy (AI Manager) listening on port {PORT}", file=sys.stderr)
    sys.stderr.flush()
    ThreadedHTTPServer(('0.0.0.0', PORT), LlamaProxy).serve_forever()
