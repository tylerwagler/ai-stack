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

# Configuration from environment
LLAMA_SERVER_HOST = os.environ.get("LLAMA_SERVER_HOST", "llama-server")
LLAMA_SERVER_PORT = os.environ.get("LLAMA_SERVER_PORT", "8082")
PORT = int(os.environ.get("PROXY_PORT", "8081"))
LLAMA_API_PREFIX = os.environ.get("LLAMA_API_PREFIX", "")
LLAMA_API_KEY = os.environ.get("LLAMA_API_KEY", "")

# DB Config
DB_NAME = os.environ.get("POSTGRES_DB", "postgres")
DB_USER = os.environ.get("POSTGRES_USER", "postgres")
DB_PASS = os.environ.get("POSTGRES_PASSWORD", "")
DB_HOST = os.environ.get("POSTGRES_HOST", "db")
DB_PORT = os.environ.get("POSTGRES_PORT", "5432")

LLAMA_BASE_URL = f"http://{LLAMA_SERVER_HOST}:{LLAMA_SERVER_PORT}{LLAMA_API_PREFIX}"

# DB Connection Pool
db_pool = None

# Cache valid keys for 60 seconds
KEY_CACHE = {}
CACHE_TTL = 30 # Reduced TTL for faster limit updates

# Cache loaded model for 30 seconds
LOADED_MODEL_CACHE = None
LOADED_MODEL_CACHE_TIME = 0
LOADED_MODEL_CACHE_TTL = 30

def get_available_models():
    """Query llama-server to get list of all available models and their status"""
    try:
        url = f"{LLAMA_BASE_URL}/v1/models"
        headers = {'Authorization': f"Bearer {LLAMA_API_KEY}"}
        response = requests.get(url, headers=headers, timeout=5)

        if response.status_code == 200:
            data = response.json()
            if data.get('data'):
                return data['data']
    except Exception as e:
        print(f"Error fetching available models: {e}", file=sys.stderr)

    return []

def get_active_model():
    """Query llama-server to find which model is currently loaded or loading. Returns None if all models are unloaded."""
    global LOADED_MODEL_CACHE, LOADED_MODEL_CACHE_TIME

    now = time.time()
    if LOADED_MODEL_CACHE and (now - LOADED_MODEL_CACHE_TIME) < LOADED_MODEL_CACHE_TTL:
        return LOADED_MODEL_CACHE

    try:
        models = get_available_models()
        if models:
            # If llama.cpp returns models, they're loaded and ready
            # (it returns 503 when no model is loaded)
            active_model = models[0]['id']
            LOADED_MODEL_CACHE = active_model
            LOADED_MODEL_CACHE_TIME = now
            print(f"Active model detected: {active_model}", file=sys.stderr)
            return active_model
    except Exception as e:
        print(f"Error detecting active model: {e}", file=sys.stderr)

    # No active model found
    return None

def get_loaded_model():
    """Query llama-server to find which model is currently loaded/loading, return None if no model loaded"""
    return get_active_model()

def model_exists(model_name):
    """Check if a specific model exists in the available models"""
    models = get_available_models()
    for model in models:
        if model.get('id') == model_name:
            return True
    return False

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
        if expiration_check and expiration_check[0]:  # If subscription expired
            print(f"USER_SUBSCRIPTION_EXPIRED: user_id={user_id}, downgraded={expiration_check[1]}", file=sys.stderr)
            conn.commit()  # Commit the tier downgrade that happened in the function

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
            # Initialize user_usage if it doesn't exist
            cur = conn.cursor()
            cur.execute("INSERT INTO public.user_usage (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
            conn.commit()
            cur.close()
            # Retry fetch
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

        # Check for resets
        now_dt = datetime.now(data["hourly_reset_at"].tzinfo if hasattr(data["hourly_reset_at"], 'tzinfo') else None)
        reset_updates = []

        # Check hourly reset
        if now_dt - data["hourly_reset_at"] > timedelta(hours=1):
            data["hourly_usage"] = 0
            data["hourly_reset_at"] = now_dt
            reset_updates.append("hourly_usage = 0, hourly_reset_at = now()")

        # Check daily reset
        if now_dt - data["daily_reset_at"] > timedelta(days=1):
            data["daily_usage"] = 0
            data["daily_reset_at"] = now_dt
            reset_updates.append("daily_usage = 0, daily_reset_at = now()")

        # Check weekly reset
        if now_dt - data["weekly_reset_at"] > timedelta(days=7):
            data["weekly_usage"] = 0
            data["weekly_reset_at"] = now_dt
            reset_updates.append("weekly_usage = 0, weekly_reset_at = now()")

        # Check monthly reset
        if now_dt - data["monthly_reset_at"] > timedelta(days=30):
            data["monthly_usage"] = 0
            data["monthly_reset_at"] = now_dt
            reset_updates.append("monthly_usage = 0, monthly_reset_at = now()")

        if reset_updates:
            cur = conn.cursor()
            cur.execute(f"UPDATE public.user_usage SET {', '.join(reset_updates)} WHERE user_id = %s", (user_id,))
            conn.commit()
            cur.close()

        KEY_CACHE[api_key] = (now, data)
        return data
    except Exception as e:
        print(f"FETCH_KEY_ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        try:
            conn.rollback()
        except:
            pass
        return None
    finally:
        if conn:
            release_db_conn(conn)

def record_usage(api_key_id, user_id, model, prompt_tokens, completion_tokens, path):
    total = prompt_tokens + completion_tokens
    print(f"RECORD_USAGE: api_key_id={api_key_id}, prompt={prompt_tokens}, completion={completion_tokens}, total={total}, model={model}", file=sys.stderr)
    conn = get_db_conn()
    if not conn: return

    try:
        cur = conn.cursor()
        # Update user-level usage counts
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

        # Update API key last_used_at
        cur.execute("""
            UPDATE public.api_keys
            SET last_used_at = now()
            WHERE id = %s
        """, (api_key_id,))

        # Log request
        cur.execute("""
            INSERT INTO public.usage_logs (api_key_id, user_id, model, prompt_tokens, completion_tokens, total_tokens, request_path)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (api_key_id, user_id, model, prompt_tokens, completion_tokens, total, path))

        conn.commit()
        cur.close()
        # Invalidate cache for this user's keys
        for k in list(KEY_CACHE.keys()):
            if KEY_CACHE[k][1] and KEY_CACHE[k][1].get("user_id") == user_id:
                del KEY_CACHE[k]
    except Exception as e:
        print(f"RECORD_USAGE_ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        try:
            conn.rollback()
        except:
            pass
    finally:
        if conn:
            release_db_conn(conn)

class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

class LlamaProxy(http.server.BaseHTTPRequestHandler):
    def handle_request(self):
        try:
            self.headers_sent = False
            
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
            print(f"REQUEST: Using API key '{key_name}' for {self.command} {self.path}", file=sys.stderr)
    
            # 2. LIMIT CHECKING (Skip for system key)
            if not key_data.get("system_key"):
                if key_data["hourly_limit"] > 0 and key_data["hourly_usage"] >= key_data["hourly_limit"]:
                    self.send_error_json(429, "Rate limit exceeded: Hourly limit reached")
                    return
                if key_data["daily_limit"] > 0 and key_data["daily_usage"] >= key_data["daily_limit"]:
                    self.send_error_json(429, "Rate limit exceeded: Daily limit reached")
                    return
                if key_data["weekly_limit"] > 0 and key_data["weekly_usage"] >= key_data["weekly_limit"]:
                    self.send_error_json(429, "Rate limit exceeded: Weekly limit reached")
                    return
                if key_data["monthly_limit"] > 0 and key_data["monthly_usage"] >= key_data["monthly_limit"]:
                    self.send_error_json(429, "Rate limit exceeded: Monthly limit reached")
                    return
    
            # Read request body
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length) if content_length > 0 else None
            
            # Rewrite Logic for JSON bodies
            model_name = "unknown"
            is_streaming = False
            if body and self.headers.get('Content-Type') == 'application/json':
                try:
                    data = json.loads(body)
                    requested_model = data.get('model', '')
                    is_streaming = data.get('stream', False)

                    # ALWAYS route to currently loaded model (admin-controlled via temper)
                    active_model = get_loaded_model()

                    if not active_model:
                        # No model loaded - return error
                        self.send_error_json(503, "No model is currently loaded. Please wait or contact administrator.")
                        return

                    # Rewrite request to use active model
                    if requested_model and requested_model != active_model:
                        print(f"[Proxy] User requested '{requested_model}', routing to '{active_model}'", file=sys.stderr)

                    data['model'] = active_model
                    body = json.dumps(data).encode('utf-8')
                    model_name = active_model
                except Exception as e:
                    print(f"Proxy Error parsing JSON: {e}", file=sys.stderr)
    
            # Prepare forwarded request
            url = f"{LLAMA_BASE_URL}{self.path}"
            print(f"Proxy Forwarding: {self.command} {self.path} -> {url}", file=sys.stderr)
            headers = {k: v for k, v in self.headers.items() if k.lower() not in ('host', 'content-length', 'authorization', 'x-api-key')}
            
            # Forward with INTERNAL API Key
            headers['Authorization'] = f"Bearer {LLAMA_API_KEY}"
            
            # DEBUG: Log incoming request info
            # print(f"DEBUG: Body: {body}", file=sys.stderr)
            
            try:
                with requests.request(
                    method=self.command,
                    url=url,
                    data=body,
                    headers=headers,
                    stream=True,
                    timeout=300
                ) as r:
                    print(f"Proxy Response: {r.status_code} for {url}", file=sys.stderr)
                    self.send_response(r.status_code)
                    excluded = ('transfer-encoding', 'content-length', 'connection', 'content-encoding')
                    for k, v in r.headers.items():
                        if k.lower() not in excluded:
                            self.send_header(k, v)
                    
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    self.headers_sent = True
                    
                    prompt_tokens = 0
                    completion_tokens = 0
                    
                    if is_streaming and r.status_code == 200:
                        content_buffer = b""
                        for chunk in r.iter_content(chunk_size=None):
                            if not chunk: continue
                            content_buffer += chunk
                            
                            while b"\n" in content_buffer:
                                line_end = content_buffer.find(b"\n")
                                line_bytes = content_buffer[:line_end]
                                content_buffer = content_buffer[line_end+1:]
                                
                                try:
                                    line = line_bytes.decode('utf-8')
                                    if line.startswith('data: '):
                                        data_str = line[6:].strip()
                                        if data_str != '[DONE]':
                                            chunk_json = json.loads(data_str)
                                            print(f"DEBUG Raw Chunk: {json.dumps(chunk_json)}", file=sys.stderr)
                                            if 'choices' in chunk_json:
                                                for choice in chunk_json['choices']:
                                                    if 'delta' in choice:
                                                        delta = choice['delta']

                                                        # Fix: Ensure content is never null for client compatibility
                                                        # We do NOT map reasoning_content to content anymore, as it confuses some clients (Cline).
                                                        if 'content' not in delta or delta['content'] is None:
                                                            delta['content'] = ""

                                            # Extract usage from both Chat Completions and Messages API formats
                                            usage = None
                                            if 'usage' in chunk_json:
                                                usage = chunk_json['usage']
                                            elif 'message' in chunk_json and 'usage' in chunk_json['message']:
                                                usage = chunk_json['message']['usage']

                                            # Also check for llama.cpp timings format
                                            if 'timings' in chunk_json:
                                                timings = chunk_json['timings']
                                                prompt_tokens = max(prompt_tokens, timings.get('prompt_n', 0))
                                                completion_tokens = max(completion_tokens, timings.get('predicted_n', 0))
                                                print(f"USAGE_EXTRACTED (timings): prompt={prompt_tokens}, completion={completion_tokens}", file=sys.stderr)
                                            elif usage:
                                                # Support both OpenAI field name conventions
                                                prompt_tokens = max(prompt_tokens, usage.get('prompt_tokens') or usage.get('input_tokens', 0))
                                                completion_tokens = max(completion_tokens, usage.get('completion_tokens') or usage.get('output_tokens', 0))
                                                print(f"USAGE_EXTRACTED (usage): prompt={prompt_tokens}, completion={completion_tokens}", file=sys.stderr)

                                            line = f"data: {json.dumps(chunk_json)}"
                                            line_bytes = line.encode('utf-8')

                                    self.wfile.write(line_bytes + b'\n')
                                    # self.wfile.flush() # Flush after each line or just at the end of the while?
                                except Exception as e:
                                    # If debugging or parsing fails, send original
                                    print(f"STREAM_PARSE_ERROR: {e}", file=sys.stderr)
                                    self.wfile.write(line_bytes + b'\n')
                                
                                self.wfile.flush()
                                
                        # Send remaining buffer
                        if content_buffer:
                            self.wfile.write(content_buffer)
                            self.wfile.flush()
                    else:
                        # Buffer non-streaming response
                        resp_body = r.content
                        
                        if r.status_code == 200:
                            try:
                                resp_json = json.loads(resp_body)
                                
                                # Compatibility Fix: Map reasoning_content to content if content is empty
                                if 'choices' in resp_json:
                                    for choice in resp_json['choices']:
                                        if 'message' in choice:
                                            msg = choice['message']
                                            if not msg.get('content') and msg.get('reasoning_content'):
                                                msg['content'] = msg['reasoning_content']
                                
                                # Use modified JSON
                                resp_body = json.dumps(resp_json).encode('utf-8')
                                print(f"DEBUG Response JSON: {json.dumps(resp_json)}", file=sys.stderr)
                                
                                # Check for llama.cpp timings format first
                                if 'timings' in resp_json:
                                    timings = resp_json['timings']
                                    prompt_tokens = timings.get('prompt_n', 0)
                                    completion_tokens = timings.get('predicted_n', 0)
                                elif 'usage' in resp_json:
                                    usage = resp_json['usage']
                                    # Support both OpenAI field name conventions
                                    prompt_tokens = usage.get('prompt_tokens') or usage.get('input_tokens', 0)
                                    completion_tokens = usage.get('completion_tokens') or usage.get('output_tokens', 0)
                            except: pass
                        
                        self.wfile.write(resp_body)
                        self.wfile.flush()
    
                    # Record usage at the end
                    print(f"DEBUG: Final tokens - prompt={prompt_tokens}, completion={completion_tokens}, system_key={key_data.get('system_key')}", file=sys.stderr)
                    if not key_data.get("system_key") and (prompt_tokens > 0 or completion_tokens > 0):
                        record_usage(
                            key_data["api_key_id"],
                            key_data["user_id"],
                            model_name,
                            prompt_tokens,
                            completion_tokens,
                            self.path
                        )
                                
            except Exception as e:
                print(f"Proxy forwarding error: {e}", file=sys.stderr)
                if not self.headers_sent:
                    self.send_error_json(502, f"Proxy Error: {str(e)}")
        except Exception as e:
            print(f"CRITICAL HANDLE REQ ERROR: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            if not self.headers_sent:
                self.send_error_json(500, f"Critical Proxy Error: {str(e)}")

    def send_error_json(self, code, message):
        try:
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            # OpenAI-compatible error format
            error_type = "rate_limit_error" if code == 429 else "invalid_request_error"
            error_response = {
                "error": {
                    "message": message,
                    "type": error_type,
                    "param": None,
                    "code": None
                }
            }
            self.wfile.write(json.dumps(error_response).encode())
        except: pass

    def do_GET(self) : self.handle_request()
    def do_POST(self) : self.handle_request()
    def do_PUT(self) : self.handle_request()
    def do_DELETE(self) : self.handle_request()

if __name__ == "__main__":
    print(f"Llama Proxy listening on port {PORT}, forwarding to {LLAMA_BASE_URL} (Auth & Tracking Enabled)", file=sys.stderr)
    sys.stderr.flush()
    ThreadedHTTPServer(('0.0.0.0', PORT), LlamaProxy).serve_forever()
