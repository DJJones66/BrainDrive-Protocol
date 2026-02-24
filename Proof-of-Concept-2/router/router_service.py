from __future__ import annotations

import json
import os
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from shared.bdp import (
    E_ADAPTER_NOT_FOUND,
    E_INTERNAL,
    E_NODE_ERROR,
    E_NODE_UNAVAILABLE,
    E_NO_ROUTE,
    E_REQUIRED_EXTENSION_MISSING,
    E_UNSUPPORTED_PROTOCOL,
    append_jsonl,
    ensure_trace,
    http_post_json,
    looks_like_bdp,
    make_error,
    new_uuid,
    now_iso,
    validate_core,
)

PORT = int(os.getenv("ROUTER_PORT", "8080"))
NODE_TIMEOUT_SEC = float(os.getenv("NODE_TIMEOUT_SEC", "3.0"))
DATA_DIR = Path(os.getenv("BDP_DATA_DIR", "/workspace/data"))
ROUTER_LOG_FILE = DATA_DIR / "router-events.jsonl"


def parse_version(version: str) -> Tuple[int, int, int]:
    parts = version.split(".")
    ints: List[int] = []
    for part in parts[:3]:
        try:
            ints.append(int(part))
        except ValueError:
            ints.append(0)
    while len(ints) < 3:
        ints.append(0)
    return ints[0], ints[1], ints[2]


def select_best(nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
    def key(node: Dict[str, Any]) -> Tuple[int, int, int, int, str]:
        major, minor, patch = parse_version(str(node.get("node_version", "0.0.0")))
        return (-int(node.get("priority", 100)), -major, -minor, -patch, str(node.get("node_id", "")))

    return sorted(nodes, key=key)[0]


REGISTRY: List[Dict[str, Any]] = [
    {
        "node_id": "terminal.echo",
        "node_version": "1.0.0",
        "supported_protocol_versions": ["0.1"],
        "capabilities": ["echo"],
        "requires": ["identity"],
        "priority": 200,
        "url": os.getenv("ECHO_URL", "http://node-echo:8091/bdp"),
    },
    {
        "node_id": "planner.alpha",
        "node_version": "0.1.0",
        "supported_protocol_versions": ["0.1"],
        "capabilities": ["plan_route"],
        "requires": [],
        "priority": 100,
        "url": os.getenv("PLANNER_URL", "http://node-planner:8094/bdp"),
    },
    {
        "node_id": "adapter.v02_to_v01",
        "node_version": "0.1.0",
        "supported_protocol_versions": ["0.2"],
        "capabilities": ["adapt_protocol"],
        "requires": [],
        "priority": 120,
        "url": os.getenv("ADAPTER_URL", "http://node-adapter:8093/bdp"),
        "target_protocol_version": "0.1",
    },
    {
        "node_id": "obs.logger",
        "node_version": "1.0.0",
        "supported_protocol_versions": ["0.1"],
        "capabilities": ["log_event"],
        "requires": [],
        "priority": 50,
        "url": os.getenv("LOGGER_URL", "http://node-logger:8092/bdp"),
    },
]


def local_log(event: str, parent_message_id: Optional[str], actor_id: Optional[str], data: Dict[str, Any]) -> None:
    append_jsonl(
        ROUTER_LOG_FILE,
        {
            "ts": now_iso(),
            "event": event,
            "parent_message_id": parent_message_id,
            "actor_id": actor_id,
            "data": data,
        },
    )


def sidecar_log(parent_message: Dict[str, Any], event: str, payload: Dict[str, Any]) -> None:
    logger_nodes = [n for n in REGISTRY if "log_event" in n.get("capabilities", []) and "0.1" in n.get("supported_protocol_versions", [])]
    if not logger_nodes:
        return

    logger = select_best(logger_nodes)
    message_id = parent_message.get("message_id")
    identity = parent_message.get("extensions", {}).get("identity")
    log_msg: Dict[str, Any] = {
        "protocol_version": "0.1",
        "message_id": new_uuid(),
        "intent": "log_event",
        "payload": {
            "event": event,
            "ts": now_iso(),
            **payload,
        },
        "extensions": {},
    }
    if identity:
        log_msg["extensions"]["identity"] = identity
    ensure_trace(log_msg, parent_message_id=message_id, hop="router.core")

    try:
        _ = http_post_json(logger["url"], log_msg, timeout_sec=1.5)
    except Exception:
        pass


def try_adapter(message: Dict[str, Any]) -> Dict[str, Any]:
    source_protocol = message.get("protocol_version")
    adapters = [
        n
        for n in REGISTRY
        if "adapt_protocol" in n.get("capabilities", []) and source_protocol in n.get("supported_protocol_versions", [])
    ]
    if not adapters:
        return make_error(
            E_ADAPTER_NOT_FOUND,
            f"No adapter found for protocol {source_protocol}",
            message.get("message_id"),
            details={"protocol_version": source_protocol},
        )

    adapter = select_best(adapters)
    to_send = deepcopy(message)
    ensure_trace(to_send, parent_message_id=message.get("message_id"), hop="router.core")

    local_log(
        "adapter_invoked",
        message.get("message_id"),
        message.get("extensions", {}).get("identity", {}).get("actor_id"),
        {"adapter_node": adapter["node_id"], "from_protocol": source_protocol, "target_protocol": adapter.get("target_protocol_version")},
    )
    sidecar_log(message, "adapter_invoked", {"adapter_node": adapter["node_id"], "from_protocol": source_protocol, "target_protocol": adapter.get("target_protocol_version")})

    try:
        adapted = http_post_json(adapter["url"], to_send, timeout_sec=NODE_TIMEOUT_SEC)
    except Exception as exc:
        return make_error(
            E_NODE_UNAVAILABLE,
            f"Adapter unavailable: {adapter['node_id']}",
            message.get("message_id"),
            retryable=True,
            details={"node_id": adapter["node_id"], "error": str(exc)},
        )

    if not looks_like_bdp(adapted):
        return make_error(
            E_NODE_ERROR,
            f"Adapter returned invalid message: {adapter['node_id']}",
            message.get("message_id"),
            details={"node_id": adapter["node_id"]},
        )

    return adapted


def try_planner(original_message: Dict[str, Any], missing_capability: str) -> Optional[Dict[str, Any]]:
    planners = [
        n
        for n in REGISTRY
        if "plan_route" in n.get("capabilities", [])
        and original_message.get("protocol_version") in n.get("supported_protocol_versions", [])
    ]
    if not planners:
        return None

    planner = select_best(planners)
    planner_msg: Dict[str, Any] = {
        "protocol_version": original_message["protocol_version"],
        "message_id": new_uuid(),
        "intent": "plan_route",
        "payload": {
            "original_message": original_message,
            "missing_capability": missing_capability,
        },
        "extensions": {},
    }

    identity = original_message.get("extensions", {}).get("identity")
    if identity:
        planner_msg["extensions"]["identity"] = identity
    ensure_trace(planner_msg, parent_message_id=original_message.get("message_id"), hop="router.core")

    local_log(
        "planner_invoked",
        original_message.get("message_id"),
        original_message.get("extensions", {}).get("identity", {}).get("actor_id"),
        {"planner_node": planner["node_id"], "missing_capability": missing_capability},
    )
    sidecar_log(original_message, "planner_invoked", {"planner_node": planner["node_id"], "missing_capability": missing_capability})

    try:
        planned = http_post_json(planner["url"], planner_msg, timeout_sec=NODE_TIMEOUT_SEC)
    except Exception:
        return None

    if not looks_like_bdp(planned):
        return None
    if planned.get("intent") == "error":
        return None

    return planned


def route_message(message: Dict[str, Any], allow_planner: bool = True, allow_adapter: bool = True) -> Dict[str, Any]:
    validation_error = validate_core(message)
    if validation_error:
        return validation_error

    msg_id = message.get("message_id")
    actor_id = message.get("extensions", {}).get("identity", {}).get("actor_id")

    protocol_candidates = [
        n for n in REGISTRY if message["protocol_version"] in n.get("supported_protocol_versions", [])
    ]
    if not protocol_candidates:
        if allow_adapter:
            adapted = try_adapter(message)
            if adapted.get("intent") == "error":
                return adapted
            return route_message(adapted, allow_planner=allow_planner, allow_adapter=False)

        return make_error(
            E_UNSUPPORTED_PROTOCOL,
            f"No nodes support protocol {message['protocol_version']}",
            msg_id,
        )

    capability = message["intent"]
    capable = [n for n in protocol_candidates if capability in n.get("capabilities", [])]
    if not capable:
        # If this protocol is not natively routable, attempt adapter translation first.
        if allow_adapter and message.get("protocol_version") != "0.1":
            adapted = try_adapter(message)
            if adapted.get("intent") == "error":
                return adapted
            return route_message(adapted, allow_planner=allow_planner, allow_adapter=False)

        if allow_planner:
            planned = try_planner(message, capability)
            if planned is not None:
                return route_message(planned, allow_planner=False, allow_adapter=allow_adapter)

        return make_error(
            E_NO_ROUTE,
            f"No node supports capability: {capability}",
            msg_id,
            details={"capability": capability},
        )

    extensions = message.get("extensions", {}) or {}
    eligible: List[Dict[str, Any]] = []
    missing_union: List[str] = []
    for node in capable:
        required = node.get("requires", [])
        missing = [req for req in required if req not in extensions]
        if missing:
            missing_union.extend(missing)
        else:
            eligible.append(node)

    if not eligible:
        missing_union = sorted(set(missing_union))
        return make_error(
            E_REQUIRED_EXTENSION_MISSING,
            "Missing required extension(s): " + ", ".join(missing_union),
            msg_id,
            details={"missing": missing_union},
        )

    selected = select_best(eligible)
    ensure_trace(message, parent_message_id=msg_id, hop="router.core")

    local_log("route_decision", msg_id, actor_id, {"selected_node": selected["node_id"], "capability": capability})
    sidecar_log(message, "route_decision", {"selected_node": selected["node_id"], "capability": capability})

    try:
        response = http_post_json(selected["url"], message, timeout_sec=NODE_TIMEOUT_SEC)
    except Exception as exc:
        return make_error(
            E_NODE_UNAVAILABLE,
            f"Node unavailable: {selected['node_id']}",
            msg_id,
            retryable=True,
            details={"node_id": selected["node_id"], "error": str(exc)},
        )

    if not looks_like_bdp(response):
        return make_error(
            E_NODE_ERROR,
            f"Node returned invalid BDP message: {selected['node_id']}",
            msg_id,
            details={"node_id": selected["node_id"]},
        )

    local_log("route_complete", msg_id, actor_id, {"selected_node": selected["node_id"], "response_intent": response.get("intent")})
    sidecar_log(message, "route_complete", {"selected_node": selected["node_id"], "response_intent": response.get("intent")})

    return response


class RouterHandler(BaseHTTPRequestHandler):
    server_version = "bdp-router/0.1"

    def _send_json(self, code: int, body: Dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"ok": True, "service": "router"})
            return
        self._send_json(404, {"ok": False})

    def do_POST(self) -> None:
        if self.path != "/route":
            self._send_json(404, {"ok": False})
            return

        try:
            size = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(size)
            message = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send_json(200, make_error("E_BAD_MESSAGE", "Invalid JSON body", None))
            return

        try:
            response = route_message(message)
        except Exception as exc:
            response = make_error(E_INTERNAL, f"Router exception: {type(exc).__name__}", message.get("message_id"), details={"error": str(exc)})

        self._send_json(200, response)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), RouterHandler)
    print(f"router listening on :{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
