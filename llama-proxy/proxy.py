import http.server
import requests
import json
import os
import sys
import socketserver
import psycopg2
import time

# Configuration from environment
LLAMA_SERVER_HOST = os.environ.get("LLAMA_SERVER_HOST", "llama-server")
LLAMA_SERVER_PORT = os.environ.get("LLAMA_SERVER_PORT", "8082")
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "glm")
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

# Cache valid keys for 60 seconds
KEY_CACHE = {}
CACHE_TTL = 60

def is_valid_api_key(api_key):
    # System key always allowed
    if api_key == LLAMA_API_KEY:
        return True
        
    now = time.time()
    if api_key in KEY_CACHE:
        entry_time, is_valid = KEY_CACHE[api_key]
        if now - entry_time < CACHE_TTL:
            return is_valid
    
    # Query database
    try:
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            host=DB_HOST,
            port=DB_PORT,
            connect_timeout=5
        )
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM public.api_keys WHERE api_key = %s LIMIT 1", (api_key,))
        result = cur.fetchone() is not None
        cur.close()
        conn.close()
        KEY_CACHE[api_key] = (now, result)
        return result
    except Exception as e:
        print(f"PROXY_DB_ERROR: {e}", file=sys.stderr)
        return False

class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

class LlamaProxy(http.server.BaseHTTPRequestHandler):
    def handle_request(self):
        self.headers_sent = False
        
        # 1. API KEY VALIDATION
        api_key = None
        if 'x-api-key' in self.headers:
            api_key = self.headers['x-api-key']
        elif 'Authorization' in self.headers:
            auth_header = self.headers['Authorization']
            if auth_header.startswith('Bearer '):
                api_key = auth_header[7:]

        if not api_key or not is_valid_api_key(api_key):
            self.send_response(401)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Unauthorized: Invalid API Key"}).encode())
            return

        # Read request body
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else None
        
        # Rewrite Logic for JSON bodies
        if body and self.headers.get('Content-Type') == 'application/json':
            try:
                data = json.loads(body)
                requested_model = data.get('model', '')
                
                # Intelligent Model Mapping
                if requested_model.startswith('claude-'):
                    target_model = DEFAULT_MODEL
                    data['model'] = target_model
                    body = json.dumps(data).encode('utf-8')
                    print(f"Proxy: Rewriting '{requested_model}' -> '{target_model}'", file=sys.stderr)
                elif not requested_model:
                    data['model'] = DEFAULT_MODEL
                    body = json.dumps(data).encode('utf-8')
            except Exception as e:
                print(f"Proxy Error parsing JSON: {e}", file=sys.stderr)

        # Prepare forwarded request
        url = f"{LLAMA_BASE_URL}{self.path}"
        headers = {k: v for k, v in self.headers.items() if k.lower() not in ('host', 'content-length', 'authorization', 'x-api-key')}
        
        # Forward with INTERNAL API Key
        headers['Authorization'] = f"Bearer {LLAMA_API_KEY}"
        
        try:
            with requests.request(
                method=self.command,
                url=url,
                data=body,
                headers=headers,
                stream=True,
                timeout=300
            ) as r:
                self.send_response(r.status_code)
                excluded = ('transfer-encoding', 'content-length', 'connection', 'content-encoding')
                for k, v in r.headers.items():
                    if k.lower() not in excluded:
                        self.send_header(k, v)
                
                self.send_header('Connection', 'close')
                self.end_headers()
                self.headers_sent = True
                
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        try:
                            self.wfile.write(chunk)
                            self.wfile.flush()
                        except: break
                            
        except Exception as e:
            print(f"Proxy forwarding error: {e}", file=sys.stderr)
            if not self.headers_sent:
                try:
                    self.send_response(502)
                    self.end_headers()
                    self.wfile.write(f"Proxy Error: {str(e)}".encode())
                except: pass

    def do_GET(self) : self.handle_request()
    def do_POST(self) : self.handle_request()
    def do_PUT(self) : self.handle_request()
    def do_DELETE(self) : self.handle_request()

if __name__ == "__main__":
    print(f"Llama Proxy listening on port {PORT}, forwarding to {LLAMA_BASE_URL} (Auth Enabled)", file=sys.stderr)
    sys.stderr.flush()
    ThreadedHTTPServer(('0.0.0.0', PORT), LlamaProxy).serve_forever()
