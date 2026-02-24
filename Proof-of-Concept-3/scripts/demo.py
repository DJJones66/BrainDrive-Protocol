from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Tuple
from urllib import request

ROOT_DIR = Path(__file__).resolve().parent.parent


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


load_dotenv(ROOT_DIR / ".env")
POC3_HOST = os.getenv("POC3_HOST", "localhost")
POC3_PORT = os.getenv("POC3_PORT", "8082")
BASE_URL = os.getenv("ROUTER_BASE_URL", f"http://{POC3_HOST}:{POC3_PORT}")


def http_json(method: str, path: str, body: Dict[str, Any] | None = None) -> Dict[str, Any]:
    data = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = request.Request(f"{BASE_URL}{path}", data=data, headers=headers, method=method)
    with request.urlopen(req, timeout=8) as resp:
        payload = resp.read().decode("utf-8")
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise RuntimeError(f"Expected JSON object from {method} {path}")
    return parsed


def route_message(message: Dict[str, Any]) -> Dict[str, Any]:
    return http_json("POST", "/route_async", message)


def get_status(message_id: str) -> Dict[str, Any]:
    return http_json("GET", f"/status/{message_id}")


def get_replay(message_id: str) -> Dict[str, Any]:
    return http_json("GET", f"/replay/{message_id}")


def get_idempotency_debug(message_id: str) -> Dict[str, Any]:
    return http_json("GET", f"/debug/idempotency/{message_id}")


def wait_for_state(message_id: str, terminal: set[str], timeout_sec: float = 25.0) -> Dict[str, Any]:
    deadline = time.time() + timeout_sec
    last: Dict[str, Any] = {}
    while time.time() < deadline:
        status_resp = get_status(message_id)
        last = status_resp
        state = str(status_resp.get("status", {}).get("state", ""))
        if state in terminal:
            return status_resp
        time.sleep(0.5)
    raise TimeoutError(f"Timed out waiting for {message_id}; last={last}")


def make_message(
    message_id: str,
    text: str,
    *,
    include_identity: bool = True,
    force_error: bool = False,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"text": text}
    if force_error:
        payload["force_error"] = True

    msg: Dict[str, Any] = {
        "protocol_version": "0.1",
        "message_id": message_id,
        "intent": "echo",
        "payload": payload,
    }
    if include_identity:
        msg["extensions"] = {
            "identity": {
                "actor_id": "user.poc3",
                "actor_type": "human",
                "roles": ["admin"],
            }
        }
    return msg


def print_case(title: str) -> None:
    print(f"\n== {title} ==")


def assert_true(condition: bool, message: str) -> Tuple[bool, str]:
    return (condition, "PASS" if condition else message)


def run_case_1_normal() -> Tuple[bool, str, str]:
    print_case("1. normal async route")
    message_id = str(uuid.uuid4())
    ack = route_message(make_message(message_id, "hello async"))
    print("ack:", json.dumps(ack, indent=2))
    status = wait_for_state(message_id, {"completed", "error", "dlq"})
    print("status:", json.dumps(status, indent=2))

    response = status.get("status", {}).get("response", {})
    ok, msg = assert_true(response.get("intent") == "echo_response", "expected echo_response")
    return ok, msg, message_id


def run_case_2_duplicate() -> Tuple[bool, str, str]:
    print_case("2. duplicate delivery idempotency")
    message_id = str(uuid.uuid4())
    msg = make_message(message_id, "duplicate test")
    ack1 = route_message(msg)
    ack2 = route_message(msg)
    print("ack1:", json.dumps(ack1, indent=2))
    print("ack2:", json.dumps(ack2, indent=2))

    status = wait_for_state(message_id, {"completed", "error", "dlq"})
    debug = get_idempotency_debug(message_id)

    print("status:", json.dumps(status, indent=2))
    print("debug:", json.dumps(debug, indent=2))

    side_effect_ok = int(debug.get("side_effect_count", 0)) == 1
    duplicate_event_ok = int(debug.get("duplicate_event_count", 0)) >= 1
    ok, msg_out = assert_true(side_effect_ok and duplicate_event_ok, "expected side_effect_count=1 and duplicate_event_count>=1")
    return ok, msg_out, message_id


def run_case_3_crash_recovery() -> Tuple[bool, str, str]:
    print_case("3. node crash/restart recovery")
    message_id = str(uuid.uuid4())

    subprocess.run(["docker", "compose", "stop", "worker-echo"], cwd=ROOT_DIR, check=True)
    try:
        ack = route_message(make_message(message_id, "recover after restart"))
        print("ack:", json.dumps(ack, indent=2))

        queued = get_status(message_id)
        print("status while worker down:", json.dumps(queued, indent=2))
        queued_state = str(queued.get("status", {}).get("state", ""))
        if queued_state != "queued":
            return False, f"expected queued while worker down, got {queued_state}", message_id
    finally:
        subprocess.run(["docker", "compose", "start", "worker-echo"], cwd=ROOT_DIR, check=True)

    status = wait_for_state(message_id, {"completed", "error", "dlq"}, timeout_sec=40.0)
    print("status after restart:", json.dumps(status, indent=2))
    response = status.get("status", {}).get("response", {})
    ok, msg = assert_true(response.get("intent") == "echo_response", "expected echo_response after restart")
    return ok, msg, message_id


def run_case_4_dlq() -> Tuple[bool, str, str]:
    print_case("4. retries and DLQ")
    message_id = str(uuid.uuid4())
    ack = route_message(make_message(message_id, "force error", force_error=True))
    print("ack:", json.dumps(ack, indent=2))

    status = wait_for_state(message_id, {"completed", "error", "dlq"}, timeout_sec=40.0)
    print("status:", json.dumps(status, indent=2))

    state = status.get("status", {}).get("state")
    error_code = status.get("status", {}).get("response", {}).get("payload", {}).get("error", {}).get("code")
    ok, msg = assert_true(state == "dlq" and error_code == "E_NODE_TIMEOUT", "expected state=dlq and code=E_NODE_TIMEOUT")
    return ok, msg, message_id


def run_case_5_replay(target_message_id: str) -> Tuple[bool, str]:
    print_case("5. replay trace")
    replay = get_replay(target_message_id)
    print("replay:", json.dumps(replay, indent=2))

    events = replay.get("events", [])
    names = {e.get("event") for e in events if isinstance(e, dict)}
    needed = {"route_enqueued", "worker_received", "worker_result"}
    ok, msg = assert_true(needed.issubset(names), f"replay missing required events: {sorted(needed - names)}")
    return ok, msg


def main() -> int:
    results: list[Tuple[str, bool, str]] = []

    ok1, msg1, _id1 = run_case_1_normal()
    results.append(("case1", ok1, msg1))

    ok2, msg2, _id2 = run_case_2_duplicate()
    results.append(("case2", ok2, msg2))

    ok3, msg3, _id3 = run_case_3_crash_recovery()
    results.append(("case3", ok3, msg3))

    ok4, msg4, id4 = run_case_4_dlq()
    results.append(("case4", ok4, msg4))

    ok5, msg5 = run_case_5_replay(id4)
    results.append(("case5", ok5, msg5))

    print("\n== summary ==")
    all_ok = True
    for name, ok, msg in results:
        print(f"{name}: {'PASS' if ok else 'FAIL'} {'' if ok else msg}")
        all_ok = all_ok and ok

    if all_ok:
        print("\nAll PoC3 scenarios passed.")
        return 0

    print("\nPoC3 scenarios failed.")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"demo fatal: {type(exc).__name__}: {exc}")
        sys.exit(1)
