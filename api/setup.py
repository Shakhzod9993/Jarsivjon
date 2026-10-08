"""Bir martalik sozlash: https://<loyiha>.vercel.app/api/setup
Supabase bucket'ini yaratadi, bazani tayyorlaydi va Telegram webhook'ini ulaydi."""

import asyncio
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from serverless.core import logger, run_setup  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        prod = (os.environ.get("VERCEL_PROJECT_PRODUCTION_URL") or "").strip()
        host = prod or self.headers.get("x-forwarded-host") or self.headers.get("Host", "")
        try:
            result = asyncio.run(run_setup("https://" + host))
            code = 200
        except Exception as e:
            logger.error(f"Setup xatosi: {e}", exc_info=True)
            result, code = {"ok": False, "xato": str(e)}, 500
        data = json.dumps(result, ensure_ascii=False, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(data)
