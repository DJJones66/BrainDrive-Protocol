from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict

from shared.bdp import (
    E_INTERNAL,
    E_NO_ROUTE,
    E_UNSUPPORTED_PROTOCOL,
    EX_CAPABILITY,
    EX_LOG,
    PROTOCOL_VERSION,
    ensure_topology,
    json_loads,
    make_error,
    publish_json,
    rabbit_connection,
    redis_append_event,
    redis_client,
    redis_get_events,
    redis_get_status,
    redis_set_status,
)

PORT = int(os.getenv("ROUTER_PORT", "8080"))

# Minimal registry for PoC3.
REGISTRY: Dict[str, Dict[str, str]] = {
    "echo": {
        "node_id": "terminal.echo",
        "routing_key": "echo",
    },
    "chat": {
        "node_id": "terminal.echo",
        "routing_key": "echo",
    },
}


def publish_log(event: str, message_id: str, details: Dict[str, Any]) -> None:
    conn = rabbit_connection()
    try:
        ch = conn.channel()
        ensure_topology(ch)
        publish_json(
            ch,
            EX_LOG,
            "",
            {
                "event": event,
                "message_id": message_id,
                "details": details,
            },
        )
    finally:
        conn.close()


class RouterHandler(BaseHTTPRequestHandler):
    server_version = "bdp-router-async/0.1"

    def _send_json(self, code: int, body: Dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _path_parts(self) -> list[str]:
        path = self.path.strip("/")
        return [] if not path else path.split("/")

    def do_GET(self) -> None:
        parts = self._path_parts()
        if self.path == "/health":
            self._send_json(200, {"ok": True, "service": "router", "mode": "async"})
            return

        if len(parts) == 2 and parts[0] == "status":
            self._handle_status(parts[1])
            return

        if len(parts) == 2 and parts[0] == "replay":
            self._handle_replay(parts[1])
            return

        if len(parts) == 3 and parts[0] == "debug" and parts[1] == "idempotency":
            self._handle_debug_idempotency(parts[2])
            return

        self._send_json(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:
        if self.path == "/route_async":
            self._handle_route_async()
            return
        if self.path == "/worker_result":
            self._handle_worker_result()
            return
        self._send_json(404, {"ok": False, "error": "not_found"})

    def _read_json(self) -> Dict[str, Any]:
        size = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(size)
        return json_loads(raw)

    def _handle_route_async(self) -> None:
        try:
            message = self._read_json()
        except Exception:
            self._send_json(200, make_error("E_BAD_MESSAGE", "Invalid JSON body", None))
            return

        msg_id = message.get("message_id")
        validation_error = None
        from shared.bdp import validate_core

        validation_error = validate_core(message)
        if validation_error:
            self._send_json(200, validation_error)
            return

        if message["protocol_version"] != PROTOCOL_VERSION:
            self._send_json(
                200,
                make_error(
                    E_UNSUPPORTED_PROTOCOL,
                    f"PoC3 router supports protocol {PROTOCOL_VERSION}",
                    msg_id,
                ),
            )
            return

        route = REGISTRY.get(message["intent"])
        if route is None:
            self._send_json(
                200,
                make_error(E_NO_ROUTE, f"No route for intent: {message['intent']}", msg_id, details={"intent": message["intent"]}),
            )
            return

        envelope = {
            "message": message,
            "node_id": route["node_id"],
            "routing_key": route["routing_key"],
            "attempt": 0,
            "max_attempts": 3,
        }

        rdb = redis_client()
        redis_set_status(
            rdb,
            msg_id,
            "queued",
            {
                "intent": message["intent"],
                "node_id": route["node_id"],
                "request": message,
                "meta": {"correlation_id": msg_id},
            },
        )
        redis_append_event(
            rdb,
            msg_id,
            "route_enqueued",
            {"node_id": route["node_id"], "routing_key": route["routing_key"], "attempt": 0},
        )

        conn = rabbit_connection()
        try:
            ch = conn.channel()
            ensure_topology(ch)
            publish_json(ch, EX_CAPABILITY, route["routing_key"], envelope)
        finally:
            conn.close()

        publish_log("route_enqueued", msg_id, {"node_id": route["node_id"], "routing_key": route["routing_key"]})

        self._send_json(
            202,
            {
                "accepted": True,
                "message_id": msg_id,
                "correlation_id": msg_id,
                "status_url": f"/status/{msg_id}",
                "replay_url": f"/replay/{msg_id}",
            },
        )

    def _handle_worker_result(self) -> None:
        try:
            body = self._read_json()
        except Exception:
            self._send_json(200, {"ok": False, "error": "bad_json"})
            return

        message_id = str(body.get("message_id", ""))
        node_id = str(body.get("node_id", "unknown"))
        response = body.get("response")
        dead_lettered = bool(body.get("dead_lettered", False))
        duplicate = bool(body.get("duplicate", False))
        attempt = int(body.get("attempt", 0))

        if not message_id:
            self._send_json(200, {"ok": False, "error": "missing_message_id"})
            return

        from shared.bdp import looks_like_bdp

        if not looks_like_bdp(response):
            response = make_error(
                "E_NODE_ERROR",
                f"Worker returned invalid BDP response: {node_id}",
                message_id,
                details={"node_id": node_id},
            )

        state = "completed"
        if response.get("intent") == "error":
            state = "dlq" if dead_lettered else "error"

        rdb = redis_client()
        redis_set_status(
            rdb,
            message_id,
            state,
            {
                "node_id": node_id,
                "response": response,
                "details": {
                    "attempt": attempt,
                    "duplicate": duplicate,
                    "dead_lettered": dead_lettered,
                },
            },
        )
        redis_append_event(
            rdb,
            message_id,
            "worker_result",
            {
                "node_id": node_id,
                "attempt": attempt,
                "duplicate": duplicate,
                "dead_lettered": dead_lettered,
                "response_intent": response.get("intent"),
            },
        )

        publish_log(
            "worker_result",
            message_id,
            {
                "node_id": node_id,
                "attempt": attempt,
                "duplicate": duplicate,
                "dead_lettered": dead_lettered,
                "response_intent": response.get("intent"),
            },
        )

        self._send_json(200, {"ok": True})

    def _handle_status(self, message_id: str) -> None:
        rdb = redis_client()
        status = redis_get_status(rdb, message_id)
        if not status:
            self._send_json(404, {"ok": False, "error": "not_found", "message_id": message_id})
            return
        self._send_json(200, {"ok": True, "message_id": message_id, "status": status})

    def _handle_replay(self, message_id: str) -> None:
        rdb = redis_client()
        status = redis_get_status(rdb, message_id)
        if not status:
            self._send_json(404, {"ok": False, "error": "not_found", "message_id": message_id})
            return
        events = redis_get_events(rdb, message_id)
        self._send_json(
            200,
            {
                "ok": True,
                "message_id": message_id,
                "request": status.get("request"),
                "response": status.get("response"),
                "state": status.get("state"),
                "events": events,
            },
        )

    def _handle_debug_idempotency(self, message_id: str) -> None:
        rdb = redis_client()
        key = f"bdp:side_effect:terminal.echo:{message_id}"
        count = rdb.get(key)
        events = redis_get_events(rdb, message_id)
        duplicate_events = [e for e in events if e.get("event") == "duplicate_delivery"]
        self._send_json(
            200,
            {
                "ok": True,
                "message_id": message_id,
                "side_effect_count": int(count) if count else 0,
                "duplicate_event_count": len(duplicate_events),
            },
        )

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    # Ensure broker topology exists before accepting traffic.
    conn = rabbit_connection()
    try:
        ch = conn.channel()
        ensure_topology(ch)
    finally:
        conn.close()

    # Ensure Redis is reachable.
    _ = redis_client()

    server = ThreadingHTTPServer(("0.0.0.0", PORT), RouterHandler)
    print(f"router async listening on :{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"router fatal error: {type(exc).__name__}: {exc}")
        raise
