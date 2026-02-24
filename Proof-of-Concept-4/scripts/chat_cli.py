from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
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


ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")
POC4_HOST = os.getenv("POC4_HOST", "localhost")
POC4_PORT = os.getenv("POC4_PORT", "8080")
DEFAULT_ROUTER_BASE = os.getenv("ROUTER_BASE_URL", f"http://{POC4_HOST}:{POC4_PORT}")


def build_message(
    prompt: str,
    node: Optional[str],
    model: Optional[str],
    max_tokens: Optional[int] = None,
    stop: Optional[List[str]] = None,
    force_async: bool = False,
) -> Dict[str, Any]:
    message: Dict[str, Any] = {
        "protocol_version": "0.1",
        "message_id": str(uuid.uuid4()),
        "intent": "chat",
        "payload": {
            "text": prompt,
        },
        "extensions": {
            "identity": {
                "actor_id": "user.chat_cli",
                "actor_type": "human",
                "roles": ["user"]
            }
        },
    }
    if force_async:
        message["payload"]["force_async"] = True
    llm: Dict[str, Any] = {}
    if node or model or max_tokens or stop:
        message["extensions"]["llm"] = {}
        if node:
            llm["node"] = node
        if model:
            llm["model"] = model
        if max_tokens and max_tokens > 0:
            llm["max_tokens"] = max_tokens
        if stop:
            llm["stop"] = stop
        message["extensions"]["llm"] = llm
    return message


def post_json(url: str, body: Dict[str, Any]) -> Dict[str, Any]:
    req = request.Request(
        url=url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode("utf-8")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("Expected JSON response")
    return parsed


def stream_sse(url: str, body: Dict[str, Any]) -> None:
    req = request.Request(
        url=url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    current_event = "message"
    current_data = ""

    with request.urlopen(req, timeout=600) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8").rstrip("\n")

            if line.startswith("event:"):
                current_event = line.split(":", 1)[1].strip()
                continue

            if line.startswith("data:"):
                current_data = line.split(":", 1)[1].strip()
                continue

            if line == "":
                payload: Dict[str, Any] = {}
                if current_data:
                    try:
                        parsed = json.loads(current_data)
                        if isinstance(parsed, dict):
                            payload = parsed
                    except Exception:
                        payload = {"raw": current_data}

                if current_event == "meta":
                    print(
                        f"\n[meta] node={payload.get('node')} model={payload.get('model')} "
                        f"message_id={payload.get('message_id')} max_tokens={payload.get('max_tokens')} "
                        f"stop_count={payload.get('stop_count', 0)}"
                    )
                elif current_event == "token":
                    print(payload.get("text", ""), end="", flush=True)
                elif current_event == "async_queued":
                    print("\n")
                    print("[async_queued]")
                    print(json.dumps(payload, indent=2))
                elif current_event == "done":
                    print("\n")
                    route_mode = payload.get("route_mode", "stream_direct")
                    token_events = payload.get("token_events")
                    output_chars = payload.get("output_chars")
                    done_reason = payload.get("ollama_done_reason")
                    print(
                        f"[done] route_mode={route_mode} token_events={token_events} "
                        f"output_chars={output_chars} reason={done_reason}"
                    )
                    break
                elif current_event == "error":
                    print("\n")
                    print(f"[error] {json.dumps(payload, indent=2)}")
                    break

                current_event = "message"
                current_data = ""


def main() -> None:
    parser = argparse.ArgumentParser(description="PoC4 streaming CLI for BrainDrive LLM router")
    parser.add_argument("prompt", nargs="?", default=None, help="Prompt text")
    parser.add_argument("--router-base", default=DEFAULT_ROUTER_BASE, help="Router base URL")
    parser.add_argument("--node", default=None, help="Node profile: general or builder")
    parser.add_argument("--model", default=None, help="Model override")
    parser.add_argument("--max-tokens", type=int, default=None, help="Generation cap mapped to Ollama num_predict")
    parser.add_argument("--stop", action="append", default=[], help="Stop sequence (repeat or comma-separate)")
    parser.add_argument("--force-async", action="store_true", help="Force async fallback queue")
    parser.add_argument("--complete", action="store_true", help="Use non-stream /complete endpoint")
    args = parser.parse_args()

    stop_sequences: List[str] = []
    for raw in args.stop:
        for token in raw.split(","):
            cleaned = token.strip()
            if cleaned:
                stop_sequences.append(cleaned)

    if not args.prompt:
        print("Enter prompt (or empty line to exit):")
        while True:
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not line:
                break

            msg = build_message(
                line,
                args.node,
                args.model,
                max_tokens=args.max_tokens,
                stop=stop_sequences,
                force_async=args.force_async,
            )
            if args.complete:
                response = post_json(f"{args.router_base}/complete", msg)
                print(json.dumps(response, indent=2))
            else:
                stream_sse(f"{args.router_base}/stream", msg)
        return

    msg = build_message(
        args.prompt,
        args.node,
        args.model,
        max_tokens=args.max_tokens,
        stop=stop_sequences,
        force_async=args.force_async,
    )
    if args.complete:
        response = post_json(f"{args.router_base}/complete", msg)
        print(json.dumps(response, indent=2))
    else:
        stream_sse(f"{args.router_base}/stream", msg)


if __name__ == "__main__":
    main()
