#!/usr/bin/env python3
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse


class ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "configs.json")
PASSWORD_FILE = os.path.join(BASE_DIR, "password.txt")
MANAGEMENT_PORT = int(os.environ.get("MANAGEMENT_PORT", "3456"))
TOKEN_EXPIRY = 86400 * 7
tokens = {}
login_attempts = {}  # ip -> {"count": int, "first_fail": float}


def load_json(path, default=None):
    if default is None:
        default = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def hash_password(password):
    return "$sha256$" + hashlib.sha256(password.encode("utf-8")).hexdigest()


def get_password_hash():
    if os.path.exists(PASSWORD_FILE):
        with open(PASSWORD_FILE, "r", encoding="utf-8") as f:
            stored = f.read().strip()
        if stored.startswith("$sha256$"):
            return stored
        # Legacy plaintext — hash and rewrite
        hashed = hash_password(stored)
        with open(PASSWORD_FILE, "w", encoding="utf-8") as f:
            f.write(hashed)
        return hashed
    # No file — create with env or default
    raw = os.environ.get("RELAY_PASSWORD", "admin")
    hashed = hash_password(raw)
    with open(PASSWORD_FILE, "w", encoding="utf-8") as f:
        f.write(hashed)
    return hashed


def verify_password(password):
    stored = get_password_hash()
    return hmac.compare_digest(stored, hash_password(password))


def check_rate_limit(ip):
    now = time.time()
    entry = login_attempts.get(ip)
    if not entry:
        return True
    # Clean up expired entries
    if now - entry["first_fail"] > 300:
        login_attempts.pop(ip, None)
        return True
    return entry["count"] < 5


def record_fail(ip):
    now = time.time()
    entry = login_attempts.get(ip)
    if not entry or now - entry["first_fail"] > 300:
        login_attempts[ip] = {"count": 1, "first_fail": now}
    else:
        entry["count"] += 1


def clear_attempts(ip):
    login_attempts.pop(ip, None)


def mask_api_key(key):
    if not key or len(key) <= 8:
        return key
    return key[:4] + "****" + key[-4:]


def check_auth(headers):
    auth = headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        entry = tokens.get(token)
        if entry and entry["expires"] > time.time():
            return True
    return False


def generate_token():
    token = secrets.token_hex(32)
    tokens[token] = {"expires": time.time() + TOKEN_EXPIRY}
    return token


def check_cors_origin(origin, path):
    """Check if origin is allowed. Returns True if ok, False to reject."""
    if not origin:
        return True
    # Only restrict /api/* routes
    if not path.startswith("/api/"):
        return True
    allowed_origins = [
        f"http://localhost:{MANAGEMENT_PORT}",
        f"http://127.0.0.1:{MANAGEMENT_PORT}",
        f"https://localhost:{MANAGEMENT_PORT}",
        f"https://127.0.0.1:{MANAGEMENT_PORT}",
    ]
    return origin in allowed_origins


def validate_config(body):
    """Validate config fields. Returns (ok, error_message)."""
    name = body.get("name", "")
    url = body.get("url", "")
    api_key = body.get("apiKey", "")

    if not isinstance(name, str) or not name.strip():
        return False, "名称不能为空"
    if len(name) > 500:
        return False, "名称过长"

    if not isinstance(url, str) or not url.strip():
        return False, "URL 不能为空"
    if len(url) > 500:
        return False, "URL 过长"
    if not url.startswith("https://"):
        return False, "URL 必须以 https:// 开头"

    if len(api_key) > 200:
        return False, "API Key 过长"

    return True, ""


class ManagementHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def get_client_ip(self):
        forwarded = self.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return self.client_address[0]

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # CORS — check origin
        origin = self.headers.get("Origin", "")
        if check_cors_origin(origin, self.path):
            cors_origin = origin if origin else f"http://localhost:{MANAGEMENT_PORT}"
            self.send_header("Access-Control-Allow-Origin", cors_origin)
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, content_type):
        try:
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_json({"error": "not found"}, 404)

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def route(self):
        parsed = urlparse(self.path)
        path = parsed.path
        method = self.command

        if method == "OPTIONS":
            origin = self.headers.get("Origin", "")
            if not check_cors_origin(origin, path):
                self.send_response(403)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
            if origin and check_cors_origin(origin, path):
                self.send_header("Access-Control-Allow-Origin", origin)
            self.end_headers()
            return

        if path == "/" or path == "/index.html":
            self.send_file(os.path.join(BASE_DIR, "index.html"), "text/html; charset=utf-8")
            return

        if path == "/vue.global.prod.js":
            self.send_file(os.path.join(BASE_DIR, "vue.global.prod.js"), "application/javascript")
            return

        if path == "/api/auth/login":
            if method == "POST":
                ip = self.get_client_ip()
                if not check_rate_limit(ip):
                    self.send_json({"error": "登录尝试过多，请稍后再试"}, 429)
                    return
                body = self.read_body()
                if verify_password(body.get("password", "")):
                    clear_attempts(ip)
                    token = generate_token()
                    self.send_json({"token": token})
                else:
                    record_fail(ip)
                    remaining = max(0, 5 - login_attempts.get(ip, {}).get("count", 0))
                    self.send_json({"error": f"密码错误 (剩余 {remaining} 次)"}, 401)
            else:
                self.send_json({"error": "Method not allowed"}, 405)
            return

        if path == "/api/auth/check":
            if check_auth(self.headers):
                self.send_json({"valid": True})
            else:
                self.send_json({"valid": False}, 401)
            return

        if path.startswith("/api/configs"):
            if not check_auth(self.headers):
                self.send_json({"error": "未登录"}, 401)
                return

            if method == "GET":
                configs = load_json(CONFIG_FILE, [])
                # Mask API keys before sending
                safe_configs = []
                for i, c in enumerate(configs):
                    sc = dict(c)
                    sc["id"] = i
                    sc["apiKey"] = mask_api_key(sc.get("apiKey", ""))
                    safe_configs.append(sc)
                self.send_json(safe_configs)

            elif method == "POST":
                body = self.read_body()
                ok, err = validate_config(body)
                if not ok:
                    self.send_json({"error": err}, 400)
                    return
                configs = load_json(CONFIG_FILE, [])
                # If key contains ****, reject on create
                api_key = body.get("apiKey", "")
                if "****" in api_key:
                    self.send_json({"error": "API Key 格式无效"}, 400)
                    return
                configs.append({
                    "platform": body.get("platform", "custom"),
                    "platformLabel": body.get("platformLabel", "自定义"),
                    "protocol": body.get("protocol", "openai"),
                    "protocolLabel": body.get("protocolLabel", "OpenAI 兼容"),
                    "name": body.get("name", "").strip(),
                    "url": body.get("url", "").strip(),
                    "apiKey": api_key.strip(),
                    "model": body.get("model", "").strip(),
                })
                save_json(CONFIG_FILE, configs)
                self.send_json({"ok": True, "id": len(configs) - 1})

            elif method == "PUT":
                configs = load_json(CONFIG_FILE, [])
                try:
                    idx = int(path.split("/")[-1])
                    if 0 <= idx < len(configs):
                        body = self.read_body()
                        ok, err = validate_config(body)
                        if not ok:
                            self.send_json({"error": err}, 400)
                            return
                        for k in ["platform", "platformLabel", "protocol", "protocolLabel", "name", "url", "model"]:
                            if k in body:
                                configs[idx][k] = body[k].strip() if isinstance(body[k], str) else body[k]
                        # Handle API key — preserve original if masked
                        if "apiKey" in body:
                            if "****" in body["apiKey"]:
                                pass  # Keep original
                            else:
                                configs[idx]["apiKey"] = body["apiKey"].strip()
                        save_json(CONFIG_FILE, configs)
                        self.send_json({"ok": True})
                    else:
                        self.send_json({"error": "索引越界"}, 400)
                except (ValueError, IndexError):
                    self.send_json({"error": "无效ID"}, 400)

            elif method == "DELETE":
                configs = load_json(CONFIG_FILE, [])
                try:
                    idx = int(path.split("/")[-1])
                    if 0 <= idx < len(configs):
                        configs.pop(idx)
                        save_json(CONFIG_FILE, configs)
                        self.send_json({"ok": True})
                    else:
                        self.send_json({"error": "索引越界"}, 400)
                except (ValueError, IndexError):
                    self.send_json({"error": "无效ID"}, 400)
            else:
                self.send_json({"error": "Method not allowed"}, 405)
            return

        self.send_json({"error": "not found"}, 404)

    def do_GET(self): self.route()
    def do_POST(self): self.route()
    def do_PUT(self): self.route()
    def do_DELETE(self): self.route()
    def do_OPTIONS(self): self.route()


if __name__ == "__main__":
    if not os.path.exists(PASSWORD_FILE):
        with open(PASSWORD_FILE, "w", encoding="utf-8") as f:
            f.write(os.environ.get("RELAY_PASSWORD", "admin"))
    # Ensure password is hashed on startup
    get_password_hash()
    if not os.path.exists(CONFIG_FILE):
        save_json(CONFIG_FILE, [])
    server = ReusableHTTPServer(("0.0.0.0", MANAGEMENT_PORT), ManagementHandler)
    print(f"[管理面板] http://0.0.0.0:{MANAGEMENT_PORT}", flush=True)
    server.serve_forever()
