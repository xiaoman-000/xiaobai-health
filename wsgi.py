# -*- coding: utf-8 -*-
"""WSGI 入口：给 PythonAnywhere 这类平台用，复用 app.py 的全部逻辑。

本地自测：
    python wsgi.py 8901     # 起一个 wsgiref 服务，仅用于验证
"""

import json
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from app import answer, WEB, RATE, RATE_LOCK, RATE_MAX, RATE_WINDOW  # noqa: E402

HEADERS = [
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "SAMEORIGIN"),
    ("Referrer-Policy", "no-referrer"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
]


def _client_key(environ):
    return (environ.get("HTTP_CF_CONNECTING_IP")
            or (environ.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
            or environ.get("REMOTE_ADDR")
            or "unknown")


def _limited(key):
    now = time.time()
    with RATE_LOCK:
        q = RATE.setdefault(key, [])
        q[:] = [t for t in q if now - t <= RATE_WINDOW]
        if len(q) >= RATE_MAX:
            return True
        q.append(now)
        if len(RATE) > 5000:
            RATE.clear()
    return False


def _json(start_response, obj, status="200 OK"):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    headers = [("Content-Type", "application/json; charset=utf-8"),
               ("Content-Length", str(len(data)))] + HEADERS
    start_response(status, headers)
    return [data]


def _text(start_response, body, ctype, status="200 OK"):
    data = body if isinstance(body, bytes) else body.encode("utf-8")
    headers = [("Content-Type", ctype), ("Content-Length", str(len(data)))] + HEADERS
    start_response(status, headers)
    return [data]


def application(environ, start_response):
    path = environ.get("PATH_INFO") or "/"
    method = environ.get("REQUEST_METHOD", "GET").upper()

    if path == "/robots.txt":
        return _text(start_response, "User-agent: *\nDisallow: /\n", "text/plain; charset=utf-8")
    if path == "/api/health":
        return _json(start_response, {"ok": True, "docs": 20})

    if path == "/api/chat":
        if method != "POST":
            return _json(start_response, {"error": "method not allowed"}, "405 Method Not Allowed")
        key = _client_key(environ)
        if _limited(key):
            return _json(start_response, {"error": "too many requests"}, "429 Too Many Requests")
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
            if length > 20000:
                return _json(start_response, {"error": "too large"}, "413 Payload Too Large")
            payload = json.loads(environ["wsgi.input"].read(length).decode("utf-8") or "{}")
            text = (payload.get("message") or "").strip()
            if not text:
                return _json(start_response, {"error": "empty"}, "400 Bad Request")
            if len(text) > 500:
                text = text[:500]
            return _json(start_response, answer(text, key))
        except Exception:
            return _json(start_response, {"error": "bad request"}, "400 Bad Request")

    # 静态文件：只暴露 web/ 目录
    rel = path.lstrip("/") or "index.html"
    full = os.path.normpath(os.path.join(WEB, rel))
    if not (full == WEB or full.startswith(WEB + os.sep)) or not os.path.isfile(full):
        return _text(start_response, "Not Found", "text/plain; charset=utf-8", "404 Not Found")
    ctype = "text/html; charset=utf-8" if full.endswith(".html") else "application/octet-stream"
    with open(full, "rb") as f:
        return _text(start_response, f.read(), ctype)


if __name__ == "__main__":
    from wsgiref.simple_server import make_server
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8901
    with make_server("127.0.0.1", port, application) as httpd:
        print("WSGI 测试服务：http://127.0.0.1:%d/" % port)
        httpd.serve_forever()
