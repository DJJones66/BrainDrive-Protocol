from __future__ import annotations

import json
import os
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict

from shared.bdp import (
    E_UNSUPPORTED_PROTOCOL,
    PROTOCOL_VERSION,
    ensure_trace,
    make_error,
    new_uuid,
    validate_core,
)

PORT = int(os.getenv("ADAPTER_PORT", "8093"))
SOURCE_PROTOCOL = "0.2"


class AdapterHandler(BaseHTTPRequestHandler):
    server_version = "adapter-v02-to-v01/0.1"

    def _send_json(self, code: int, body: Dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"ok": True, "service": "adapter.v02_to_v01"})
            return
        self._send_json(404, {"ok": False})

    def do_POST(self) -> None:
        if self.path != "/bdp":
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

        if message["protocol_version"] != SOURCE_PROTOCOL:
            self._send_json(
                200,
                make_error(
                    E_UNSUPPORTED_PROTOCOL,
                    f"adapter.v02_to_v01 only accepts protocol {SOURCE_PROTOCOL}",
                    message.get("message_id"),
                ),
            )
            return

        translated: Dict[str, Any] = {
            "protocol_version": PROTOCOL_VERSION,
            "message_id": new_uuid(),
            "intent": message["intent"],
            "payload": deepcopy(message["payload"]),
            "extensions": deepcopy(message.get("extensions", {}) or {}),
        }
        translated.setdefault("extensions", {})
        translated["extensions"]["adapter"] = {
            "from_protocol": SOURCE_PROTOCOL,
            "to_protocol": PROTOCOL_VERSION,
            "adapter_node": "adapter.v02_to_v01",
        }

        ensure_trace(translated, parent_message_id=message.get("message_id"), hop="adapter.v02_to_v01")
        self._send_json(200, translated)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), AdapterHandler)
    print(f"adapter.v02_to_v01 listening on :{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
