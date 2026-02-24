from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple
from urllib import request


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


load_dotenv(Path(__file__).resolve().parent.parent / ".env")
POC2_HOST = os.getenv("POC2_HOST", "localhost")
POC2_PORT = os.getenv("POC2_PORT", "8080")
ROUTER_URL = os.getenv("ROUTER_URL", f"http://{POC2_HOST}:{POC2_PORT}/route")


def send(message: Dict[str, Any]) -> Dict[str, Any]:
    data = json.dumps(message).encode("utf-8")
    req = request.Request(ROUTER_URL, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with request.urlopen(req, timeout=5) as resp:
        body = resp.read().decode("utf-8")
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise RuntimeError("Router response was not a JSON object")
    return parsed


def bdp_message(protocol_version: str, intent: str, text: str, include_identity: bool = True) -> Dict[str, Any]:
    msg: Dict[str, Any] = {
        "protocol_version": protocol_version,
        "message_id": str(uuid.uuid4()),
        "intent": intent,
        "payload": {"text": text},
    }
    if include_identity:
        msg["extensions"] = {
            "identity": {
                "actor_id": "user.demo",
                "actor_type": "human",
                "roles": ["admin"],
            },
            "features": {
                "supports_identity": True,
                "supports_permissions": False,
            },
        }
    return msg


def error_code(response: Dict[str, Any]) -> str:
    return str(response.get("payload", {}).get("error", {}).get("code", ""))


def run_case(name: str, message: Dict[str, Any], check: Callable[[Dict[str, Any]], Tuple[bool, str]]) -> bool:
    print(f"\n== {name} ==")
    print("request:")
    print(json.dumps(message, indent=2))

    response = send(message)

    print("response:")
    print(json.dumps(response, indent=2))

    ok, details = check(response)
    print("result:", "PASS" if ok else f"FAIL ({details})")
    return ok


def main() -> int:
    cases: List[Tuple[str, Dict[str, Any], Callable[[Dict[str, Any]], Tuple[bool, str]]]] = [
        (
            "echo with identity",
            bdp_message("0.1", "echo", "Hello from PoC2", include_identity=True),
            lambda r: (r.get("intent") == "echo_response", f"expected echo_response, got {r.get('intent')}")
        ),
        (
            "echo missing identity",
            bdp_message("0.1", "echo", "Should fail", include_identity=False),
            lambda r: (error_code(r) == "E_REQUIRED_EXTENSION_MISSING", f"expected E_REQUIRED_EXTENSION_MISSING, got {error_code(r)}")
        ),
        (
            "planner fallback say_hi -> echo",
            bdp_message("0.1", "say_hi", "there", include_identity=True),
            lambda r: (
                r.get("intent") == "echo_response" and str(r.get("payload", {}).get("text", "")).startswith("Hi!"),
                f"expected planner-mapped echo_response, got {r.get('intent')}"
            )
        ),
        (
            "adapter fallback 0.2 -> 0.1",
            bdp_message("0.2", "echo", "Adapter path", include_identity=True),
            lambda r: (r.get("intent") == "echo_response", f"expected echo_response, got {r.get('intent')}")
        ),
        (
            "no adapter for 0.3",
            bdp_message("0.3", "echo", "No adapter", include_identity=True),
            lambda r: (error_code(r) == "E_ADAPTER_NOT_FOUND", f"expected E_ADAPTER_NOT_FOUND, got {error_code(r)}")
        ),
    ]

    all_ok = True
    for name, message, check in cases:
        if not run_case(name, message, check):
            all_ok = False

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
