#!/usr/bin/env python3
import os
import sys
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

CONFIG_FILE = os.path.join(BASE_DIR, "configs.json")
PASSWORD_FILE = os.path.join(BASE_DIR, "password.txt")


def ensure_files():
    import json
    if not os.path.exists(PASSWORD_FILE):
        with open(PASSWORD_FILE, "w", encoding="utf-8") as f:
            f.write(os.environ.get("RELAY_PASSWORD", "admin"))
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)


def run_management():
    from server import ReusableHTTPServer, ManagementHandler, MANAGEMENT_PORT
    server = ReusableHTTPServer(("0.0.0.0", MANAGEMENT_PORT), ManagementHandler)
    print(f"[管理面板] http://0.0.0.0:{MANAGEMENT_PORT}", flush=True)
    server.serve_forever()


def run_relay():
    import uvicorn
    port = int(os.environ.get("PROXY_PORT", "11434"))
    print(f"[代理服务] http://0.0.0.0:{port}  (Ollama 兼容)", flush=True)
    uvicorn.run(
        "relay:app",
        host="0.0.0.0",
        port=port,
        log_level="warning",
    )


if __name__ == "__main__":
    ensure_files()
    print("=" * 50, flush=True)
    print("  Ollama Relay Station (基于 oai2ollama)", flush=True)
    print("=" * 50, flush=True)

    t = threading.Thread(target=run_management, daemon=True)
    t.start()

    run_relay()
