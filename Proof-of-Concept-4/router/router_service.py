from __future__ import annotations

import json
import os
import re
import socket
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib import error, request

PROTOCOL_VERSION = "0.1"

E_BAD_MESSAGE = "E_BAD_MESSAGE"
E_UNSUPPORTED_PROTOCOL = "E_UNSUPPORTED_PROTOCOL"
E_NODE_UNAVAILABLE = "E_NODE_UNAVAILABLE"
E_NODE_ERROR = "E_NODE_ERROR"


def parse_int_env(name: str, default: int, min_value: int = 1) -> int:
    raw = str(os.getenv(name, str(default))).strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    if value < min_value:
        return default
    return value


ROUTER_PORT = int(os.getenv("ROUTER_PORT", "8080"))
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
DATA_DIR = Path(os.getenv("BDP_DATA_DIR", "/workspace/data"))
EVENTS_FILE = DATA_DIR / "events.jsonl"
UI_FILE = Path(__file__).resolve().parent / "static" / "index.html"

ASYNC_FALLBACK_ENABLED = str(os.getenv("ASYNC_FALLBACK_ENABLED", "true")).strip().lower() in {"1", "true", "yes", "on"}
ASYNC_FALLBACK_MIN_CHARS = int(os.getenv("ASYNC_FALLBACK_MIN_CHARS", "700"))
ASYNC_FALLBACK_ROUTE_URL = str(os.getenv("ASYNC_FALLBACK_ROUTE_URL", "")).strip()
ASYNC_FALLBACK_STATUS_BASE = str(os.getenv("ASYNC_FALLBACK_STATUS_BASE", "")).strip().rstrip("/")
OLLAMA_DEFAULT_MAX_TOKENS = parse_int_env("OLLAMA_DEFAULT_MAX_TOKENS", 512, min_value=1)
OLLAMA_DEFAULT_STOP = [s.strip() for s in str(os.getenv("OLLAMA_DEFAULT_STOP", "")).split(",") if s.strip()]

NODE_PROFILES: Dict[str, Dict[str, str]] = {
    "general": {
        "node_id": "node.assistant.general",
        "default_model": "ministral-3:8b",
        "system_prompt": (
            "You are the BrainDrive general assistant node. "
            "Answer clearly, directly, and keep responses useful for engineering work."
        ),
    },
    "builder": {
        "node_id": "node.assistant.builder",
        "default_model": "qwen3:8b",
        "system_prompt": (
            "You are the BrainDrive builder node. "
            "Provide implementation-first guidance, concrete steps, and production-minded tradeoffs."
        ),
    },
}

DIRECTIVE_NODE_RE = re.compile(r"^/node:([^\s]+)$")
DIRECTIVE_MODEL_RE = re.compile(r"^/model:([^\s]+)$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def append_event(entry: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with EVENTS_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def make_error(parent_message_id: Optional[str], code: str, message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "message_id": new_id(),
        "intent": "error",
        "payload": {
            "error": {
                "code": code,
                "message": message,
                "retryable": False,
                "details": details or {},
            }
        },
        "extensions": {
            "trace": {
                "parent_message_id": parent_message_id,
                "depth": 1,
                "path": ["router.stream"],
            }
        },
    }


def validate_message(message: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(message, dict):
        return make_error(None, E_BAD_MESSAGE, "Message must be an object")

    msg_id = message.get("message_id")
    for field in ("protocol_version", "message_id", "intent", "payload"):
        if field not in message:
            return make_error(msg_id, E_BAD_MESSAGE, f"Missing required field: {field}")

    if not isinstance(message.get("protocol_version"), str):
        return make_error(msg_id, E_BAD_MESSAGE, "protocol_version must be string")
    if not isinstance(message.get("message_id"), str):
        return make_error(msg_id, E_BAD_MESSAGE, "message_id must be string")
    if not isinstance(message.get("intent"), str):
        return make_error(msg_id, E_BAD_MESSAGE, "intent must be string")
    if not isinstance(message.get("payload"), dict):
        return make_error(msg_id, E_BAD_MESSAGE, "payload must be object")

    if message["protocol_version"] != PROTOCOL_VERSION:
        return make_error(
            msg_id,
            E_UNSUPPORTED_PROTOCOL,
            f"PoC4 supports protocol version {PROTOCOL_VERSION}",
            {"received": message["protocol_version"]},
        )

    if message["intent"] not in {"chat", "prompt", "ask"}:
        return make_error(msg_id, E_BAD_MESSAGE, "intent must be one of: chat, prompt, ask")

    text = message["payload"].get("text")
    if not isinstance(text, str):
        return make_error(msg_id, E_BAD_MESSAGE, "payload.text must be string")

    return None


def parse_directives(text: str) -> Dict[str, Optional[str]]:
    selected_node: Optional[str] = None
    selected_model: Optional[str] = None
    cleaned_tokens = []

    for token in text.split():
        node_match = DIRECTIVE_NODE_RE.match(token)
        if node_match:
            selected_node = node_match.group(1).strip()
            continue

        model_match = DIRECTIVE_MODEL_RE.match(token)
        if model_match:
            selected_model = model_match.group(1).strip()
            continue

        cleaned_tokens.append(token)

    return {
        "node": selected_node,
        "model": selected_model,
        "prompt": " ".join(cleaned_tokens).strip(),
    }


def post_json(url: str, payload: Dict[str, Any], timeout_sec: float = 15.0) -> Dict[str, Any]:
    req = request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout_sec) as resp:
        raw = resp.read().decode("utf-8")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("Expected JSON object response")
    return parsed


def get_models() -> Dict[str, Any]:
    req = request.Request(url=f"{OLLAMA_BASE_URL}/api/tags", method="GET")
    with request.urlopen(req, timeout=10.0) as resp:
        raw = resp.read().decode("utf-8")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("Unexpected /api/tags response")
    return parsed


def absolute_url(path_or_url: Optional[str]) -> Optional[str]:
    if not path_or_url:
        return None
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return path_or_url
    if ASYNC_FALLBACK_STATUS_BASE:
        if path_or_url.startswith("/"):
            return f"{ASYNC_FALLBACK_STATUS_BASE}{path_or_url}"
        return f"{ASYNC_FALLBACK_STATUS_BASE}/{path_or_url}"
    return path_or_url


class RouterHandler(BaseHTTPRequestHandler):
    server_version = "bdp-stream-router/0.2"

    def _send_json(self, status: int, body: Dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def _send_html(self, status: int, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def _send_empty(self, status: int = 204) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def _start_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def _sse(self, event: str, data: Dict[str, Any]) -> None:
        chunk = f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8")
        self.wfile.write(chunk)
        self.wfile.flush()

    def _read_json(self) -> Dict[str, Any]:
        size = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(size)
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("JSON body must be object")
        return parsed

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path in {"/", "/ui"}:
            if UI_FILE.exists():
                self._send_html(200, UI_FILE.read_text(encoding="utf-8"))
            else:
                self._send_json(500, {"ok": False, "error": "ui_not_found"})
            return

        if self.path == "/favicon.ico":
            self._send_empty(204)
            return

        if self.path == "/api":
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "bdp-stream-router",
                    "endpoints": ["/health", "/nodes", "/models", "/complete", "/stream", "/ui", "/favicon.ico"],
                    "async_fallback_enabled": ASYNC_FALLBACK_ENABLED,
                    "async_fallback_min_chars": ASYNC_FALLBACK_MIN_CHARS,
                    "async_fallback_route_url": ASYNC_FALLBACK_ROUTE_URL,
                    "async_fallback_status_base": ASYNC_FALLBACK_STATUS_BASE,
                    "ollama_default_max_tokens": OLLAMA_DEFAULT_MAX_TOKENS,
                    "ollama_default_stop": OLLAMA_DEFAULT_STOP,
                },
            )
            return

        if self.path == "/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "bdp-stream-router",
                    "ollama_base_url": OLLAMA_BASE_URL,
                    "async_fallback_enabled": ASYNC_FALLBACK_ENABLED,
                    "ollama_default_max_tokens": OLLAMA_DEFAULT_MAX_TOKENS,
                    "ollama_default_stop": OLLAMA_DEFAULT_STOP,
                },
            )
            return

        if self.path == "/nodes":
            self._send_json(200, {"ok": True, "nodes": NODE_PROFILES})
            return

        if self.path == "/models":
            try:
                tags = get_models()
                model_names = [m.get("name") for m in tags.get("models", []) if isinstance(m, dict) and isinstance(m.get("name"), str)]
                self._send_json(200, {"ok": True, "models": model_names})
            except Exception as exc:
                self._send_json(502, {"ok": False, "error": f"Failed to query Ollama models: {type(exc).__name__}: {exc}"})
            return

        self._send_json(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:
        if self.path == "/complete":
            self._handle_complete()
            return

        if self.path == "/stream":
            self._handle_stream()
            return

        self._send_json(404, {"ok": False, "error": "not_found"})

    def _resolve_target(self, message: Dict[str, Any]) -> Dict[str, str]:
        payload = message.get("payload", {})
        extensions = message.get("extensions", {}) or {}
        ext_llm = extensions.get("llm", {}) if isinstance(extensions, dict) else {}

        raw_text = str(payload.get("text", ""))
        directives = parse_directives(raw_text)

        requested_node = directives.get("node") or (ext_llm.get("node") if isinstance(ext_llm, dict) else None) or "general"
        requested_model = directives.get("model") or (ext_llm.get("model") if isinstance(ext_llm, dict) else None)
        cleaned_prompt = directives.get("prompt") or raw_text.strip()

        node_key = str(requested_node)
        if node_key not in NODE_PROFILES:
            node_key = "general"

        profile = NODE_PROFILES[node_key]
        model = str(requested_model or profile["default_model"])

        return {
            "node": node_key,
            "node_id": profile["node_id"],
            "model": model,
            "prompt": cleaned_prompt,
            "system_prompt": profile["system_prompt"],
        }

    def _parse_stop_sequences(self, value: Any) -> List[str]:
        if isinstance(value, str):
            candidates = [value]
        elif isinstance(value, list):
            candidates = [item for item in value if isinstance(item, str)]
        else:
            return []

        stop_sequences: List[str] = []
        for raw in candidates:
            token = raw.strip()
            if token and token not in stop_sequences:
                stop_sequences.append(token)
        return stop_sequences

    def _resolve_ollama_options(self, message: Dict[str, Any]) -> Dict[str, Any]:
        payload = message.get("payload", {})
        extensions = message.get("extensions", {}) or {}
        ext_llm = extensions.get("llm", {}) if isinstance(extensions, dict) else {}

        raw_max_tokens: Any = None
        if isinstance(ext_llm, dict):
            if "max_tokens" in ext_llm:
                raw_max_tokens = ext_llm.get("max_tokens")
            elif "num_predict" in ext_llm:
                raw_max_tokens = ext_llm.get("num_predict")
        if raw_max_tokens is None and isinstance(payload, dict):
            raw_max_tokens = payload.get("max_tokens")

        max_tokens = OLLAMA_DEFAULT_MAX_TOKENS
        if raw_max_tokens is not None:
            try:
                parsed = int(raw_max_tokens)
                if parsed > 0:
                    max_tokens = parsed
            except (TypeError, ValueError):
                pass

        raw_stop: Any = None
        if isinstance(ext_llm, dict) and "stop" in ext_llm:
            raw_stop = ext_llm.get("stop")
        elif isinstance(payload, dict):
            raw_stop = payload.get("stop")

        if raw_stop is None:
            stop_sequences = list(OLLAMA_DEFAULT_STOP)
        else:
            stop_sequences = self._parse_stop_sequences(raw_stop)

        options: Dict[str, Any] = {"num_predict": max_tokens}
        if stop_sequences:
            options["stop"] = stop_sequences
        return options

    def _ollama_chat(self, model: str, system_prompt: str, prompt: str, options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        }
        if options:
            payload["options"] = options
        return post_json(f"{OLLAMA_BASE_URL}/api/chat", payload, timeout_sec=180.0)

    def _should_async_fallback(self, message: Dict[str, Any], target: Dict[str, str]) -> Tuple[bool, str]:
        if not ASYNC_FALLBACK_ENABLED:
            return False, "disabled"

        payload = message.get("payload", {})
        extensions = message.get("extensions", {}) or {}
        routing_ext = extensions.get("routing", {}) if isinstance(extensions, dict) else {}

        force_async = bool(payload.get("force_async", False))
        if isinstance(routing_ext, dict):
            force_async = force_async or bool(routing_ext.get("force_async", False))

        if force_async:
            return True, "forced"

        if len(target["prompt"]) >= ASYNC_FALLBACK_MIN_CHARS:
            return True, "prompt_too_long"

        return False, "not_needed"

    def _build_async_message(self, original_message: Dict[str, Any], target: Dict[str, str]) -> Dict[str, Any]:
        async_message: Dict[str, Any] = {
            "protocol_version": PROTOCOL_VERSION,
            "message_id": original_message["message_id"],
            "intent": "chat",
            "payload": {
                "text": target["prompt"],
                "source": "poc4_stream_router",
                "route_mode": "async_fallback",
            },
            "extensions": {
                "llm": {
                    "node": target["node"],
                    "node_id": target["node_id"],
                    "model": target["model"],
                    "system_prompt": target["system_prompt"],
                },
                "trace": {
                    "parent_message_id": original_message["message_id"],
                    "depth": 1,
                    "path": ["router.async_fallback"],
                },
            },
        }

        identity = original_message.get("extensions", {}).get("identity")
        if isinstance(identity, dict):
            async_message["extensions"]["identity"] = identity
        else:
            async_message["extensions"]["identity"] = {"actor_id": "user.poc4.fallback", "actor_type": "human", "roles": ["user"]}

        return async_message

    def _queue_async(self, message: Dict[str, Any], target: Dict[str, str], reason: str) -> Dict[str, Any]:
        if not ASYNC_FALLBACK_ROUTE_URL:
            raise RuntimeError("ASYNC_FALLBACK_ROUTE_URL is not configured")

        async_message = self._build_async_message(message, target)
        ack = post_json(ASYNC_FALLBACK_ROUTE_URL, async_message, timeout_sec=20.0)

        if not bool(ack.get("accepted", False)):
            raise RuntimeError(f"Async fallback did not accept message: {ack}")

        return {
            "accepted": True,
            "message_id": ack.get("message_id", message["message_id"]),
            "correlation_id": ack.get("correlation_id", message["message_id"]),
            "status_url": absolute_url(ack.get("status_url")),
            "replay_url": absolute_url(ack.get("replay_url")),
            "reason": reason,
            "min_chars": ASYNC_FALLBACK_MIN_CHARS,
            "route_url": ASYNC_FALLBACK_ROUTE_URL,
        }

    def _handle_complete(self) -> None:
        try:
            message = self._read_json()
        except Exception:
            self._send_json(400, make_error(None, E_BAD_MESSAGE, "Invalid JSON body"))
            return

        validation_error = validate_message(message)
        if validation_error:
            self._send_json(200, validation_error)
            return

        target = self._resolve_target(message)
        ollama_options = self._resolve_ollama_options(message)
        msg_id = message["message_id"]

        if not target["prompt"]:
            self._send_json(200, make_error(msg_id, E_BAD_MESSAGE, "Prompt is empty after directive parsing"))
            return

        should_fallback, reason = self._should_async_fallback(message, target)
        if should_fallback:
            try:
                queued = self._queue_async(message, target, reason)
                append_event(
                    {
                        "ts": now_iso(),
                        "event": "complete_async_queued",
                        "message_id": msg_id,
                        "node": target["node"],
                        "node_id": target["node_id"],
                        "model": target["model"],
                        "reason": reason,
                    }
                )
                self._send_json(
                    202,
                    {
                        "protocol_version": PROTOCOL_VERSION,
                        "message_id": new_id(),
                        "intent": "accepted_async",
                        "payload": {
                            "text": "Prompt queued to durable async pipeline",
                            "route_mode": "async_fallback",
                            **queued,
                        },
                        "extensions": {
                            "trace": {
                                "parent_message_id": msg_id,
                                "depth": 1,
                                "path": ["router.complete", "router.async_fallback"],
                            }
                        },
                    },
                )
                return
            except Exception as exc:
                self._send_json(
                    200,
                    make_error(
                        msg_id,
                        E_NODE_UNAVAILABLE,
                        f"Async fallback unavailable: {type(exc).__name__}",
                        {"error": str(exc), "async_route": ASYNC_FALLBACK_ROUTE_URL},
                    ),
                )
                return

        try:
            ollama = self._ollama_chat(target["model"], target["system_prompt"], target["prompt"], ollama_options)
            content = ollama.get("message", {}).get("content", "") if isinstance(ollama, dict) else ""
            response = {
                "protocol_version": PROTOCOL_VERSION,
                "message_id": new_id(),
                "intent": "chat_response",
                "payload": {
                    "text": content,
                    "node": target["node"],
                    "node_id": target["node_id"],
                    "model": target["model"],
                    "route_mode": "stream_direct",
                    "ollama_done_reason": ollama.get("done_reason") if isinstance(ollama, dict) else None,
                },
                "extensions": {
                    "trace": {
                        "parent_message_id": msg_id,
                        "depth": 1,
                        "path": ["router.complete", target["node_id"]],
                    }
                },
            }

            append_event(
                {
                    "ts": now_iso(),
                    "event": "complete",
                    "message_id": msg_id,
                    "node": target["node"],
                    "node_id": target["node_id"],
                    "model": target["model"],
                    "max_tokens": ollama_options.get("num_predict"),
                    "stop_count": len(ollama_options.get("stop", [])),
                    "prompt_preview": target["prompt"][:120],
                    "response_preview": str(content)[:120],
                }
            )
            self._send_json(200, response)
        except Exception as exc:
            self._send_json(
                200,
                make_error(
                    msg_id,
                    E_NODE_UNAVAILABLE,
                    f"Ollama unavailable: {type(exc).__name__}",
                    {"error": str(exc), "ollama_base_url": OLLAMA_BASE_URL},
                ),
            )

    def _handle_stream(self) -> None:
        try:
            message = self._read_json()
        except Exception:
            self._send_json(400, make_error(None, E_BAD_MESSAGE, "Invalid JSON body"))
            return

        validation_error = validate_message(message)
        if validation_error:
            self._send_json(200, validation_error)
            return

        target = self._resolve_target(message)
        ollama_options = self._resolve_ollama_options(message)
        msg_id = message["message_id"]

        if not target["prompt"]:
            self._send_json(200, make_error(msg_id, E_BAD_MESSAGE, "Prompt is empty after directive parsing"))
            return

        self._start_sse()

        try:
            should_fallback, reason = self._should_async_fallback(message, target)
            self._sse(
                "meta",
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "message_id": msg_id,
                    "node": target["node"],
                    "node_id": target["node_id"],
                    "model": target["model"],
                    "async_fallback": should_fallback,
                    "async_reason": reason,
                    "max_tokens": ollama_options.get("num_predict"),
                    "stop_count": len(ollama_options.get("stop", [])),
                },
            )

            if should_fallback:
                queued = self._queue_async(message, target, reason)
                self._sse("async_queued", queued)
                self._sse(
                    "done",
                    {
                        "message_id": msg_id,
                        "node": target["node"],
                        "node_id": target["node_id"],
                        "model": target["model"],
                        "route_mode": "async_fallback",
                    },
                )
                append_event(
                    {
                        "ts": now_iso(),
                        "event": "stream_async_queued",
                        "message_id": msg_id,
                        "node": target["node"],
                        "node_id": target["node_id"],
                        "model": target["model"],
                        "reason": reason,
                    }
                )
                return

            req_payload = {
                "model": target["model"],
                "messages": [
                    {"role": "system", "content": target["system_prompt"]},
                    {"role": "user", "content": target["prompt"]},
                ],
                "stream": True,
            }
            if ollama_options:
                req_payload["options"] = ollama_options

            req = request.Request(
                url=f"{OLLAMA_BASE_URL}/api/chat",
                data=json.dumps(req_payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            full_text = ""
            token_count = 0
            done_payload: Dict[str, Any] = {}

            with request.urlopen(req, timeout=600.0) as resp:
                for raw in resp:
                    line = raw.decode("utf-8").strip()
                    if not line:
                        continue

                    try:
                        part = json.loads(line)
                    except Exception:
                        self._sse("error", {"code": E_NODE_ERROR, "message": "Invalid Ollama stream chunk", "raw": line[:200]})
                        continue

                    if isinstance(part, dict) and "error" in part:
                        self._sse(
                            "error",
                            {
                                "code": E_NODE_ERROR,
                                "message": str(part.get("error")),
                                "model": target["model"],
                            },
                        )
                        break

                    piece = ""
                    if isinstance(part, dict):
                        message_obj = part.get("message")
                        if isinstance(message_obj, dict):
                            piece = str(message_obj.get("content", ""))

                    if piece:
                        token_count += 1
                        full_text += piece
                        self._sse("token", {"text": piece})

                    if isinstance(part, dict) and bool(part.get("done", False)):
                        done_payload = part
                        break

            self._sse(
                "done",
                {
                    "message_id": msg_id,
                    "node": target["node"],
                    "node_id": target["node_id"],
                    "model": target["model"],
                    "route_mode": "stream_direct",
                    "token_events": token_count,
                    "output_chars": len(full_text),
                    "ollama_done_reason": done_payload.get("done_reason"),
                    "max_tokens": ollama_options.get("num_predict"),
                    "stop_count": len(ollama_options.get("stop", [])),
                },
            )

            append_event(
                {
                    "ts": now_iso(),
                    "event": "stream_complete",
                    "message_id": msg_id,
                    "node": target["node"],
                    "node_id": target["node_id"],
                    "model": target["model"],
                    "prompt_preview": target["prompt"][:120],
                    "output_chars": len(full_text),
                    "token_events": token_count,
                    "ollama_done_reason": done_payload.get("done_reason"),
                }
            )
        except BrokenPipeError:
            append_event(
                {
                    "ts": now_iso(),
                    "event": "client_disconnected",
                    "message_id": msg_id,
                    "node": target["node"],
                    "model": target["model"],
                }
            )
        except error.URLError as exc:
            self._sse(
                "error",
                {
                    "code": E_NODE_UNAVAILABLE,
                    "message": f"Ollama request failed: {exc}",
                    "ollama_base_url": OLLAMA_BASE_URL,
                },
            )
        except Exception as exc:
            self._sse(
                "error",
                {
                    "code": E_NODE_ERROR,
                    "message": f"Router stream error: {type(exc).__name__}",
                    "details": {"error": str(exc)},
                },
            )
        finally:
            self.close_connection = True
            try:
                self.wfile.flush()
            except Exception:
                pass
            try:
                self.connection.shutdown(socket.SHUT_WR)
            except Exception:
                pass

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"bdp stream router listening on :{ROUTER_PORT}")
    print(f"ollama base url: {OLLAMA_BASE_URL}")
    print(f"ollama default max tokens: {OLLAMA_DEFAULT_MAX_TOKENS}")
    print(f"ollama default stop count: {len(OLLAMA_DEFAULT_STOP)}")
    print(f"async fallback enabled: {ASYNC_FALLBACK_ENABLED}")
    if ASYNC_FALLBACK_ENABLED:
        print(f"async fallback route: {ASYNC_FALLBACK_ROUTE_URL}")
        print(f"async fallback min chars: {ASYNC_FALLBACK_MIN_CHARS}")
    server = ThreadingHTTPServer(("0.0.0.0", ROUTER_PORT), RouterHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
