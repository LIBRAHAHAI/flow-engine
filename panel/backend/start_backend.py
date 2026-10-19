"""start_backend.py — 后端 API 后台启动器（launcher.py 同款语义）。

以 CREATE_NEW_PROCESS_GROUP 派生 uvicorn 子进程并立即返回（detached），
父进程（双击/终端）退出后后端继续运行。端口已占用时直接退出提示。

用法：
  python start_backend.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import urllib.request
from pathlib import Path

API_PORT = 8650
BACKEND_DIR = Path(__file__).resolve().parent


def is_alive() -> bool:
    try:
        with urllib.request.urlopen(
                f"http://localhost:{API_PORT}/api/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def main() -> int:
    if is_alive():
        print(f"✅ 后端已在运行（端口 {API_PORT}）")
        return 0
    code = ("import uvicorn; from api import app; "
            f"uvicorn.run(app, host='0.0.0.0', port={API_PORT}, "
            "log_level='warning')")
    subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=str(BACKEND_DIR),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=0x00000200 if sys.platform == "win32" else 0,
    )
    for _ in range(15):
        if is_alive():
            print(f"✅ 后端已就绪（端口 {API_PORT}）")
            return 0
        import time
        time.sleep(1)
    print(f"❌ 后端启动失败（{API_PORT} 未就绪）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
