"""Telegram webhook: https://<loyiha>.vercel.app/api/webhook"""

import asyncio
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from serverless.core import DownloadError, handle_update, logger, webhook_secret  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(200, {"ok": True, "info": "Webhook ishlayapti. Telegram bu yerga POST yuboradi."})

    def do_POST(self):
        got = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(got, webhook_secret()):
            self._send(403, {"ok": False})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._send(400, {"ok": False})
            return
        try:
            asyncio.run(handle_update(payload))
        except DownloadError as e:
            # Baza yuklanmadi -> 500, Telegram update'ni keyinroq qayta yuboradi
            logger.error(f"Baza yuklanmadi: {e}")
            self._send(500, {"ok": False})
            return
        except Exception as e:
            # Boshqa xatolarda 200 qaytaramiz, aks holda Telegram bir xil xabarni qayta-qayta yuboradi
            logger.error(f"Update qayta ishlashda xato: {e}", exc_info=True)
        self._send(200, {"ok": True})
