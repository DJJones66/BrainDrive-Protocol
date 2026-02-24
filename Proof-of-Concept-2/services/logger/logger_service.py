from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict

from shared.bdp import PROTOCOL_VERSION, append_jsonl, make_error, new_uuid, now_iso, validate_core

PORT = int(os.getenv("LOGGER_PORT", "8092"))
DATA_DIR = Path(os.getenv("BDP_DATA_DIR", "/workspace/data"))
LOG_FILE = DATA_DIR / "logger-events.jsonl"


class LoggerHandler(BaseHTTPRequestHandler):
    server_version = "obs-logger/1.0"

    def _send_json(self, code: int, body: Dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"ok": True, "service": "obs.logger"})
            return
        self._send_json(404, {"ok": False})

    def do_POST(self) -> None:
        if self.path != "/ancp":
            self._send_json(404, {"ok": False})
            return

        try:
            size = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(size)
            message = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send_json(200, make_error("E_BAD_MESSAGE", "Invalid JSON body", None))
            return

        validation_error = validate_core(message)
        if validation_error:
            self._send_json(200, validation_error)
            return

        if message["intent"] != "log_event":
            self._send_json(200, make_error("E_NO_ROUTE", "obs.logger only handles log_event", message.get("message_id")))
            return

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        append_jsonl(
            LOG_FILE,
            {
                "ts": now_iso(),
                "message_id": message.get("message_id"),
                "protocol_version": message.get("protocol_version"),
                "payload": message.get("payload", {}),
                "identity": message.get("extensions", {}).get("identity", {}),
                "trace": message.get("extensions", {}).get("trace", {}),
            },
        )

        response = {
            "protocol_version": PROTOCOL_VERSION,
            "message_id": new_uuid(),
            "intent": "response",
            "payload": {"ok": True, "logged": True},
        }
        self._send_json(200, response)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), LoggerHandler)
    print(f"obs.logger listening on :{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
