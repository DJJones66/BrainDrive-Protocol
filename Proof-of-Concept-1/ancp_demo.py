from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROTOCOL_VERSION = "0.1"

E_BAD_MESSAGE = "E_BAD_MESSAGE"
E_UNSUPPORTED_PROTOCOL = "E_UNSUPPORTED_PROTOCOL"
E_NO_ROUTE = "E_NO_ROUTE"
E_REQUIRED_EXTENSION_MISSING = "E_REQUIRED_EXTENSION_MISSING"

DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR = Path(os.getenv("BDP_DATA_DIR", str(DEFAULT_DATA_DIR)))
EVENT_LOG_FILE = DATA_DIR / "events.jsonl"

REGISTRY: List[Dict[str, Any]] = [
    {
        "node_id": "terminal.echo",
        "node_version": "1.0.0",
        "supported_protocol_versions": [PROTOCOL_VERSION],
        "capabilities": ["echo"],
        "requires": ["identity"],
        "priority": 200,
    },
    {
        "node_id": "planner.alpha",
        "node_version": "0.1.0",
        "supported_protocol_versions": [PROTOCOL_VERSION],
        "capabilities": ["plan_route"],
        "requires": [],
        "priority": 100,
    },
    {
        "node_id": "obs.logger",
        "node_version": "1.0.0",
        "supported_protocol_versions": [PROTOCOL_VERSION],
        "capabilities": ["log_event"],
        "requires": [],
        "priority": 50,
    },
]


def new_uuid() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def log_event(parent_message_id: Optional[str], event: str, data: Dict[str, Any], actor_id: Optional[str]) -> None:
    ensure_data_dir()
    entry = {
        "ts": now_iso(),
        "event": event,
        "parent_message_id": parent_message_id,
        "actor_id": actor_id,
        "data": data,
    }
    with EVENT_LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def make_error(code: str, message: str, parent_message_id: Optional[str], details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    err: Dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "message_id": new_uuid(),
        "intent": "error",
        "payload": {
            "error": {
                "code": code,
                "message": message,
                "retryable": False,
                "details": details or {},
            }
        },
    }
    if parent_message_id:
        err["extensions"] = {
            "trace": {
                "parent_message_id": parent_message_id,
                "depth": 0,
                "path": [],
            }
        }
    return err


def validate_message(message: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(message, dict):
        return make_error(E_BAD_MESSAGE, "Message must be an object", None)

    msg_id = message.get("message_id")
    for field in ("protocol_version", "message_id", "intent", "payload"):
        if field not in message:
            return make_error(E_BAD_MESSAGE, f"Missing required field: {field}", msg_id)

    if not isinstance(message["protocol_version"], str):
        return make_error(E_BAD_MESSAGE, "protocol_version must be string", msg_id)
    if not isinstance(message["message_id"], str):
        return make_error(E_BAD_MESSAGE, "message_id must be string", msg_id)
    if not isinstance(message["intent"], str):
        return make_error(E_BAD_MESSAGE, "intent must be string", msg_id)
    if not isinstance(message["payload"], dict):
        return make_error(E_BAD_MESSAGE, "payload must be object", msg_id)
    if "extensions" in message and message["extensions"] is not None and not isinstance(message["extensions"], dict):
        return make_error(E_BAD_MESSAGE, "extensions must be object if present", msg_id)

    return None


def ensure_trace(message: Dict[str, Any]) -> None:
    ext = message.setdefault("extensions", {})
    trace = ext.setdefault(
        "trace",
        {
            "parent_message_id": message["message_id"],
            "depth": 0,
            "path": [],
        },
    )
    trace["depth"] = int(trace.get("depth", 0)) + 1
    trace.setdefault("path", [])
    trace["path"].append("router.core")


def select_best(nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
    return sorted(nodes, key=lambda n: (-int(n.get("priority", 100)), n.get("node_id", "")))[0]


def planner_fallback(original_message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if original_message["intent"] != "say_hi":
        return None

    text = str(original_message.get("payload", {}).get("text", "")).strip() or "Hello!"
    planned: Dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "message_id": new_uuid(),
        "intent": "echo",
        "payload": {"text": f"Hi! {text}"},
        "extensions": {},
    }

    original_ext = original_message.get("extensions", {})
    if isinstance(original_ext, dict) and "identity" in original_ext:
        planned["extensions"]["identity"] = original_ext["identity"]

    planned["extensions"]["trace"] = {
        "parent_message_id": original_message["message_id"],
        "depth": 1,
        "path": ["router.core", "planner.alpha"],
    }
    return planned


def node_terminal_echo(message: Dict[str, Any]) -> Dict[str, Any]:
    text = str(message.get("payload", {}).get("text", ""))
    actor_id = message.get("extensions", {}).get("identity", {}).get("actor_id", "unknown")

    trace = message.get("extensions", {}).get("trace", {})
    path = list(trace.get("path", []))
    path.append("terminal.echo")

    return {
        "protocol_version": PROTOCOL_VERSION,
        "message_id": new_uuid(),
        "intent": "echo_response",
        "payload": {
            "text": text,
            "handled_by": "terminal.echo",
            "actor": actor_id,
        },
        "extensions": {
            "trace": {
                "parent_message_id": message["message_id"],
                "depth": int(trace.get("depth", 0)) + 1,
                "path": path,
            }
        },
    }


def route(message: Dict[str, Any]) -> Dict[str, Any]:
    validation_error = validate_message(message)
    if validation_error:
        return validation_error

    msg_id = message["message_id"]
    actor_id = message.get("extensions", {}).get("identity", {}).get("actor_id")

    candidates = [
        n for n in REGISTRY if message["protocol_version"] in n.get("supported_protocol_versions", [])
    ]
    if not candidates:
        return make_error(
            E_UNSUPPORTED_PROTOCOL,
            f"No nodes support protocol {message['protocol_version']}",
            msg_id,
        )

    capability = message["intent"]
    capable = [n for n in candidates if capability in n.get("capabilities", [])]
    if not capable:
        planned = planner_fallback(message)
        if planned is not None:
            log_event(
                msg_id,
                "planner_invoked",
                {"planner_node": "planner.alpha", "missing_capability": capability},
                actor_id,
            )
            return route(planned)
        return make_error(E_NO_ROUTE, f"No node supports capability: {capability}", msg_id)

    extensions = message.get("extensions", {}) or {}
    eligible = []
    missing_union: List[str] = []
    for node in capable:
        required = node.get("requires", [])
        missing = [req for req in required if req not in extensions]
        if not missing:
            eligible.append(node)
        else:
            missing_union.extend(missing)

    if not eligible:
        missing_union = sorted(set(missing_union))
        return make_error(
            E_REQUIRED_EXTENSION_MISSING,
            "Missing required extension(s): " + ", ".join(missing_union),
            msg_id,
            {"missing": missing_union},
        )

    selected = select_best(eligible)
    ensure_trace(message)
    log_event(msg_id, "route_decision", {"selected_node": selected["node_id"], "capability": capability}, actor_id)

    if selected["node_id"] == "terminal.echo":
        response = node_terminal_echo(message)
    else:
        response = make_error(E_NO_ROUTE, "Selected node not implemented", msg_id)

    log_event(
        msg_id,
        "route_complete",
        {"selected_node": selected["node_id"], "response_intent": response.get("intent")},
        actor_id,
    )
    return response


def cli_to_message(line: str) -> Optional[Dict[str, Any]]:
    line = line.strip()
    if not line:
        return None
    if line.lower() in ("quit", "exit"):
        return {"_meta": {"control": "quit"}}
    if line.lower() in ("help", "?"):
        return {"_meta": {"control": "help"}}

    parts = line.split()
    intent = parts[0]
    args = parts[1:]
    include_identity = True

    if args and args[0] == "--no-id":
        include_identity = False
        args = args[1:]

    text = " ".join(args)
    msg: Dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "message_id": new_uuid(),
        "intent": intent,
        "payload": {"text": text},
    }

    if include_identity:
        msg["extensions"] = {
            "identity": {
                "actor_id": "user.local",
                "actor_type": "human",
                "roles": ["admin"],
            }
        }
    return msg


def render_response(response: Dict[str, Any]) -> None:
    intent = response.get("intent")
    if intent == "echo_response":
        payload = response.get("payload", {})
        print(payload.get("text", ""))
        print(f"  [meta] handled_by={payload.get('handled_by')} actor={payload.get('actor')}")
        return
    if intent == "error":
        err = response.get("payload", {}).get("error", {})
        print(f"ERROR {err.get('code')}: {err.get('message')}")
        details = err.get("details") or {}
        if details:
            print(f"  details={details}")
        return
    print(json.dumps(response, indent=2))


def print_help() -> None:
    print(
        "\nCommands:\n"
        "  echo <text...>         -> routes to terminal.echo (requires identity)\n"
        "  echo --no-id <text...> -> simulate missing identity\n"
        "  say_hi <text...>       -> planner fallback maps to echo\n"
        "  help                   -> show this help\n"
        "  quit                   -> exit\n"
    )


def main() -> None:
    ensure_data_dir()
    print("BDP v0.1 PoC running. Type 'help' for commands.\n")
    while True:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        message = cli_to_message(line)
        if message is None:
            continue
        if "_meta" in message:
            control = message["_meta"].get("control")
            if control == "quit":
                print("Bye.")
                break
            if control == "help":
                print_help()
                continue

        response = route(message)
        render_response(response)


if __name__ == "__main__":
    main()
