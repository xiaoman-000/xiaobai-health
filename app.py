# -*- coding: utf-8 -*-
"""小白 · 本地网页版：一个进程同时提供页面和对话接口。

运行：
    python app.py            # 默认 http://127.0.0.1:8765/
    python app.py --open     # 启动后自动打开浏览器
"""

import os
import re
import sys
import json
import time
import socket
import threading
from collections import deque
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from xiaobai_agent import run, DISCLAIMER  # noqa: E402

WEB = os.path.join(BASE, "web")

# 每个访问者各自保存上一轮上下文，避免多人同时使用时串话
LAST = {}
LAST_LOCK = threading.Lock()

# 简易限流：同一来源 60 秒内最多 40 次请求
RATE = {}
RATE_LOCK = threading.Lock()
RATE_WINDOW = 60
RATE_MAX = 40

DEPT = {
    "胸痛": "心内科；如果正在胸痛、出大汗，直接去急诊",
    "呼吸困难": "呼吸内科；喘得厉害直接去急诊",
    "晕厥": "神经内科或心内科，先排查原因",
    "抽搐": "神经内科；首次发作建议急诊",
    "呕血": "消化内科急诊",
    "黑便": "消化内科急诊",
    "嘴歪": "神经内科急诊",
    "说话不清": "神经内科急诊",
    "发热": "发热门诊或感染科",
    "头痛": "神经内科",
    "头晕": "神经内科；伴心慌可加心内科",
    "咽痛": "耳鼻喉科",
    "咳嗽": "呼吸内科",
    "腹泻": "消化内科",
    "呕吐": "消化内科",
    "腹痛": "消化内科或普外科",
    "感冒": "呼吸内科或全科",
    "失眠": "精神心理科或睡眠门诊",
    "过敏": "皮肤科；有休克表现直接急诊",
}


def emergency_number(flag):
    return "12356" if flag == "自伤自杀念头" else "120"


class Handler(BaseHTTPRequestHandler):
    server_version = "XiaobaiLocal/1.0"

    def log_message(self, fmt, *args):
        pass

    def _client_key(self):
        ip = (self.headers.get("CF-Connecting-IP")
              or (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
              or self.client_address[0])
        return ip or "unknown"

    def _too_many(self):
        key = self._client_key()
        now = time.time()
        with RATE_LOCK:
            q = RATE.setdefault(key, deque())
            while q and now - q[0] > RATE_WINDOW:
                q.popleft()
            if len(q) >= RATE_MAX:
                return True
            q.append(now)
            if len(RATE) > 5000:
                RATE.clear()
        return False

    def _send(self, code, body, content_type="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if code == 429:
            self.send_header("Retry-After", str(RATE_WINDOW))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/robots.txt":
            return self._send(200, "User-agent: *\nDisallow: /\n", "text/plain; charset=utf-8")
        if path in ("/", "/index.html"):
            full = os.path.join(WEB, "index.html")
        elif path == "/api/health":
            return self._send(200, json.dumps({"ok": True, "docs": 20}, ensure_ascii=False))
        else:
            full = os.path.normpath(os.path.join(WEB, path.lstrip("/")))
            if not full.startswith(WEB + os.sep) and full != WEB:
                return self._send(404, "Not Found", "text/plain; charset=utf-8")
        if not os.path.isfile(full):
            return self._send(404, "Not Found", "text/plain; charset=utf-8")
        ctype = "text/html; charset=utf-8" if full.endswith(".html") else "application/octet-stream"
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_POST(self):
        if self.path.split("?")[0] != "/api/chat":
            return self._send(404, json.dumps({"error": "not found"}))
        if self._too_many():
            return self._send(429, json.dumps({"error": "too many requests"}, ensure_ascii=False))
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > 20000:
            return self._send(413, json.dumps({"error": "too large"}, ensure_ascii=False))
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception:
            return self._send(400, json.dumps({"error": "bad json"}, ensure_ascii=False))

        text = (payload.get("message") or "").strip()
        if not text:
            return self._send(400, json.dumps({"error": "empty"}, ensure_ascii=False))
        if len(text) > 500:
            text = text[:500]

        body = answer(text, self._client_key())
        self._send(200, json.dumps(body, ensure_ascii=False))


def answer(text, key=None):
    """一次完整的对话：多轮承接优先，其余走九节点主链路。"""
    t0 = time.time()
    try:
        reply = context_reply(text, key)
        if reply is None:
            trace = run(text)
            out = trace["output"]
            m1 = trace["output"]["trace"].get("node_2_m1") or {}
            with LAST_LOCK:
                LAST[key] = {"m1": m1, "risk": out["risk_level"]}
                if len(LAST) > 2000:
                    LAST.clear()
            inner = trace["output"]["trace"]
            flag = (inner.get("node_8_fallback") or {}).get("flag")
            m2 = inner.get("node_6_m2") or {}
            if out.get("need_emergency"):
                evidence = ["安全兜底 · " + flag] if flag else ["安全兜底"]
            else:
                evidence = m2.get("evidence") if m2.get("kb_hit") else []
            body = {
                "reply_text": out["reply_text"],
                "voice_text": out["voice_text"],
                "followups": out["followups"],
                "risk_level": out["risk_level"],
                "need_emergency": bool(out.get("need_emergency")),
                "meta": {
                    "route": "安全兜底" if out.get("need_emergency") else (inner.get("node_5_task_route") or "直答"),
                    "flag": flag,
                    "emergency_number": emergency_number(flag) if out.get("need_emergency") else "",
                    "kb_hit": bool(m2.get("kb_hit")) or bool(flag),
                    "evidence": evidence or [],
                    "latency_ms": round((time.time() - t0) * 1000, 1),
                },
            }
        else:
            body = reply
        body["ok"] = True
        return body
    except Exception as exc:
        return {
            "ok": True,
            "reply_text": "我这边刚刚卡了一下，你再说一遍好吗？\n" + DISCLAIMER,
            "voice_text": "我这边刚刚卡了一下 你再说一遍好吗 我不是医生 这些建议不能替代面诊",
            "followups": ["我再试一次", "换个说法说"],
            "risk_level": "中",
            "need_emergency": False,
            "meta": {"route": "异常兜底", "kb_hit": False, "evidence": [], "error": str(exc)[:80]},
        }


def context_reply(text, key=None):
    """极轻量的多轮承接：只处理「挂什么科」这类追问，其余交给主链路。"""
    if not re.search(r"(挂什么科|哪个科|看什么科|去哪科|什么科室|看哪一科)", text):
        return None
    with LAST_LOCK:
        saved = dict(LAST.get(key) or {})
    m1 = saved.get("m1") or {}
    risk = saved.get("risk")
    symptoms = m1.get("symptoms") or []
    if not symptoms:
        return None
    s = symptoms[0]
    dept = DEPT.get(s, "全科或内科")
    person = m1.get("person") or "未知"
    if person == "儿童":
        first = "孩子的话，先挂儿科；发热的孩子也可以直接去发热门诊。"
    elif person == "孕妇":
        first = "孕期优先去产科；其他不舒服可以在产科一并问。"
    elif person == "老人":
        first = "老人可以先挂老年科或内科。"
    else:
        first = "按你刚才说的" + s + "，建议先挂" + dept + "。"
    reply_text = (
        first + "\n"
        + "如果中途出现胸痛、喘不上气、意识不清、抽搐、呕血黑便这些情况，别等门诊，直接去急诊。\n"
        + DISCLAIMER
    )
    voice = first.replace("；", " ") + " 出现危险信号直接去急诊 我不是医生 这些建议不能替代面诊"
    return {
        "reply_text": reply_text,
        "voice_text": voice,
        "followups": ["需要带什么资料", "这种情况常见吗", "多久能好"],
        "risk_level": risk or "中",
        "need_emergency": False,
        "meta": {"route": "多轮承接", "kb_hit": True, "evidence": ["上一轮对话"], "latency_ms": 1.0},
    }


def free_port(start=8765):
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit("没有可用端口")


def main():
    host = os.environ.get("XIAOBAI_HOST", "127.0.0.1")
    want = int(os.environ.get("XIAOBAI_PORT", os.environ.get("PORT", "8765")))
    fixed = bool(os.environ.get("PORT") or os.environ.get("XIAOBAI_PORT"))
    port = want if fixed else free_port(want)
    server = ThreadingHTTPServer((host, port), Handler)
    shown = "127.0.0.1" if host in ("0.0.0.0", "") else host
    url = "http://%s:%d/" % (shown, port)
    print("小白已启动：" + url)
    print("按 Ctrl+C 停止。")
    if "--open" in sys.argv:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
