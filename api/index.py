"""Bosh sahifa (tekshiruv uchun): https://<loyiha>.vercel.app/api"""

import json
from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        data = json.dumps(
            {"ok": True, "bot": "telegram_auto", "setup": "/api/setup", "webhook": "/api/webhook"},
            ensure_ascii=False,
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(data)
