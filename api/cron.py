"""Kunlik qarz eslatmalari. Vercel Cron vercel.json'dagi jadval bo'yicha chaqiradi."""

import asyncio
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from serverless.core import logger, run_daily_reminders  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        secret = (os.environ.get("CRON_SECRET") or "").strip()
        got = self.headers.get("Authorization", "")
        if not secret or not hmac.compare_digest(got, f"Bearer {secret}"):
            self._send(401, {"ok": False, "xato": "CRON_SECRET noto'g'ri yoki kiritilmagan"})
            return
        try:
            result = asyncio.run(run_daily_reminders())
            self._send(200, {"ok": True, **(result or {})})
        except Exception as e:
            logger.error(f"Cron xatosi: {e}", exc_info=True)
            self._send(500, {"ok": False, "xato": str(e)})

    def _send(self, code, body):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(data)
