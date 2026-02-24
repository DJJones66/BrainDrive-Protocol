from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict

from shared.bdp import (
    E_NO_ROUTE,
    E_REQUIRED_EXTENSION_MISSING,
    E_UNSUPPORTED_PROTOCOL,
    PROTOCOL_VERSION,
    ensure_trace,
    make_error,
    new_uuid,
    validate_core,
)

PORT = int(os.getenv("ECHO_PORT", "8091"))


class EchoHandler(BaseHTTPRequestHandler):
    server_version = "terminal-echo/1.0"

    def _send_json(self, code: int, body: Dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"ok": True, "service": "terminal.echo"})
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

        if message["protocol_version"] != PROTOCOL_VERSION:
            self._send_json(
                200,
                make_error(
                    E_UNSUPPORTED_PROTOCOL,
                    f"terminal.echo only supports protocol {PROTOCOL_VERSION}",
                    message.get("message_id"),
                ),
            )
            return

        if message["intent"] != "echo":
            self._send_json(
                200,
                make_error(
                    E_NO_ROUTE,
                    f"terminal.echo does not handle intent {message['intent']}",
                    message.get("message_id"),
                ),
            )
            return

        extensions = message.get("extensions", {}) or {}
        if "identity" not in extensions:
            self._send_json(
                200,
                make_error(
                    E_REQUIRED_EXTENSION_MISSING,
                    "Missing required extension(s): identity",
                    message.get("message_id"),
                    details={"missing": ["identity"]},
                ),
            )
            return

        actor_id = extensions.get("identity", {}).get("actor_id", "unknown")
        text = str(message.get("payload", {}).get("text", ""))

        response: Dict[str, Any] = {
            "protocol_version": PROTOCOL_VERSION,
            "message_id": new_uuid(),
            "intent": "echo_response",
            "payload": {
                "text": text,
                "handled_by": "terminal.echo",
                "actor": actor_id,
            },
            "extensions": {
                "identity": extensions.get("identity", {}),
            },
        }
        ensure_trace(response, parent_message_id=message.get("message_id"), hop="terminal.echo")
        self._send_json(200, response)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), EchoHandler)
    print(f"terminal.echo listening on :{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
