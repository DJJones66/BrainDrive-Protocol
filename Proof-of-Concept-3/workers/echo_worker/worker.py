from __future__ import annotations

import json
import os
import time
from typing import Any, Dict

from shared.bdp import (
    E_NODE_TIMEOUT,
    E_REQUIRED_EXTENSION_MISSING,
    E_UNSUPPORTED_PROTOCOL,
    EX_CAPABILITY,
    EX_DLQ,
    EX_LOG,
    PROTOCOL_VERSION,
    QUEUE_ECHO,
    ensure_topology,
    ensure_trace,
    json_loads,
    make_error,
    post_json,
    publish_json,
    rabbit_connection,
    redis_append_event,
    redis_client,
)

NODE_ID = os.getenv("WORKER_NODE_ID", "terminal.echo")
ROUTER_RESULT_URL = os.getenv("ROUTER_RESULT_URL", "http://router:8080/worker_result")
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "3"))
RETRY_DELAY_SEC = float(os.getenv("RETRY_DELAY_SEC", "1.0"))
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
DEFAULT_CHAT_MODEL = os.getenv("DEFAULT_CHAT_MODEL", "ministral-3:8b")

DEFAULT_SYSTEM_PROMPTS = {
    "general": "You are the BrainDrive general assistant node. Answer clearly and concisely.",
    "builder": "You are the BrainDrive builder node. Provide implementation-first guidance.",
}


def publish_log(channel: Any, message_id: str, event: str, details: Dict[str, Any]) -> None:
    publish_json(
        channel,
        EX_LOG,
        "",
        {
            "event": event,
            "message_id": message_id,
            "details": details,
        },
    )


def send_result(message_id: str, response: Dict[str, Any], attempt: int, duplicate: bool, dead_lettered: bool) -> None:
    payload = {
        "message_id": message_id,
        "node_id": NODE_ID,
        "response": response,
        "attempt": attempt,
        "duplicate": duplicate,
        "dead_lettered": dead_lettered,
    }
    _ = post_json(ROUTER_RESULT_URL, payload, timeout_sec=5.0)


def build_echo_response(message: Dict[str, Any]) -> Dict[str, Any]:
    text = str(message.get("payload", {}).get("text", ""))
    actor_id = message.get("extensions", {}).get("identity", {}).get("actor_id", "unknown")
    response: Dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "message_id": message.get("message_id"),
        "intent": "echo_response",
        "payload": {
            "text": text,
            "handled_by": NODE_ID,
            "actor": actor_id,
        },
        "extensions": {},
    }
    if "identity" in (message.get("extensions", {}) or {}):
        response["extensions"]["identity"] = message["extensions"]["identity"]
    ensure_trace(response, parent_message_id=message.get("message_id"), hop="terminal.echo.worker")
    return response


def build_chat_response(message: Dict[str, Any]) -> Dict[str, Any]:
    ext = message.get("extensions", {}) or {}
    llm = ext.get("llm", {}) if isinstance(ext, dict) else {}

    text = str(message.get("payload", {}).get("text", ""))
    node = str(llm.get("node", "general")) if isinstance(llm, dict) else "general"
    model = str(llm.get("model", DEFAULT_CHAT_MODEL)) if isinstance(llm, dict) else DEFAULT_CHAT_MODEL
    node_id = str(llm.get("node_id", f"node.assistant.{node}")) if isinstance(llm, dict) else f"node.assistant.{node}"

    default_prompt = DEFAULT_SYSTEM_PROMPTS.get(node, DEFAULT_SYSTEM_PROMPTS["general"])
    system_prompt = str(llm.get("system_prompt", default_prompt)) if isinstance(llm, dict) else default_prompt

    ollama_req = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        "stream": False,
    }

    ollama_resp = post_json(f"{OLLAMA_BASE_URL}/api/chat", ollama_req, timeout_sec=300.0)
    content = ""
    if isinstance(ollama_resp, dict):
        message_obj = ollama_resp.get("message", {})
        if isinstance(message_obj, dict):
            content = str(message_obj.get("content", ""))

    response: Dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "message_id": message.get("message_id"),
        "intent": "chat_response",
        "payload": {
            "text": content,
            "handled_by": NODE_ID,
            "node": node,
            "node_id": node_id,
            "model": model,
        },
        "extensions": {},
    }

    if "identity" in (message.get("extensions", {}) or {}):
        response["extensions"]["identity"] = message["extensions"]["identity"]

    ensure_trace(response, parent_message_id=message.get("message_id"), hop="terminal.echo.worker.chat")
    return response


def process_delivery(channel: Any, body: bytes) -> None:
    envelope = json_loads(body)
    message = envelope.get("message")
    attempt = int(envelope.get("attempt", 0))
    max_attempts = int(envelope.get("max_attempts", MAX_ATTEMPTS))

    if not isinstance(message, dict):
        return

    message_id = str(message.get("message_id", ""))
    if not message_id:
        return

    rdb = redis_client()
    redis_append_event(rdb, message_id, "worker_received", {"node_id": NODE_ID, "attempt": attempt})
    publish_log(channel, message_id, "worker_received", {"node_id": NODE_ID, "attempt": attempt})

    idempotency_key = f"bdp:idempotency:{NODE_ID}:{message_id}"
    side_effect_key = f"bdp:side_effect:{NODE_ID}:{message_id}"
    cached_response_key = f"bdp:node_response:{NODE_ID}:{message_id}"

    if message.get("protocol_version") != PROTOCOL_VERSION:
        response = make_error(
            E_UNSUPPORTED_PROTOCOL,
            f"{NODE_ID} supports protocol {PROTOCOL_VERSION}",
            message_id,
        )
        rdb.set(cached_response_key, json.dumps(response))
        send_result(message_id, response, attempt=attempt, duplicate=False, dead_lettered=False)
        return

    extensions = message.get("extensions", {}) or {}
    if "identity" not in extensions:
        response = make_error(
            E_REQUIRED_EXTENSION_MISSING,
            "Missing required extension(s): identity",
            message_id,
            details={"missing": ["identity"]},
        )
        rdb.set(cached_response_key, json.dumps(response))
        redis_append_event(rdb, message_id, "worker_error", {"node_id": NODE_ID, "code": E_REQUIRED_EXTENSION_MISSING})
        publish_log(channel, message_id, "worker_error", {"node_id": NODE_ID, "code": E_REQUIRED_EXTENSION_MISSING})
        send_result(message_id, response, attempt=attempt, duplicate=False, dead_lettered=False)
        return

    force_error = bool(message.get("payload", {}).get("force_error", False))
    if not force_error:
        first_seen = bool(rdb.set(idempotency_key, "1", nx=True))
        if not first_seen:
            cached = rdb.get(cached_response_key)
            if cached:
                response = json_loads(cached)
            else:
                response = make_error("E_NODE_ERROR", f"Duplicate delivery but no cached response for {NODE_ID}", message_id)
            redis_append_event(rdb, message_id, "duplicate_delivery", {"node_id": NODE_ID, "attempt": attempt})
            publish_log(channel, message_id, "duplicate_delivery", {"node_id": NODE_ID, "attempt": attempt})
            send_result(message_id, response, attempt=attempt, duplicate=True, dead_lettered=False)
            return

    if force_error:
        next_attempt = attempt + 1
        if next_attempt < max_attempts:
            retry_envelope = {
                "message": message,
                "node_id": NODE_ID,
                "routing_key": "echo",
                "attempt": next_attempt,
                "max_attempts": max_attempts,
            }
            time.sleep(RETRY_DELAY_SEC)
            publish_json(channel, EX_CAPABILITY, "echo", retry_envelope)
            redis_append_event(rdb, message_id, "retry_scheduled", {"node_id": NODE_ID, "attempt": next_attempt})
            publish_log(channel, message_id, "retry_scheduled", {"node_id": NODE_ID, "attempt": next_attempt})
            return

        response = make_error(
            E_NODE_TIMEOUT,
            f"{NODE_ID} exceeded max attempts",
            message_id,
            retryable=True,
            details={"node_id": NODE_ID, "attempt": next_attempt},
        )
        dead_letter_entry = {
            "message": message,
            "node_id": NODE_ID,
            "attempt": next_attempt,
            "dead_lettered": True,
            "error": response,
        }
        publish_json(channel, EX_DLQ, "echo", dead_letter_entry)
        redis_append_event(rdb, message_id, "worker_dead_lettered", {"node_id": NODE_ID, "attempt": next_attempt})
        publish_log(channel, message_id, "worker_dead_lettered", {"node_id": NODE_ID, "attempt": next_attempt})
        rdb.set(cached_response_key, json.dumps(response))
        send_result(message_id, response, attempt=next_attempt, duplicate=False, dead_lettered=True)
        return

    rdb.set(side_effect_key, "1")

    try:
        intent = str(message.get("intent", ""))
        if intent == "chat":
            response = build_chat_response(message)
        elif intent == "echo":
            response = build_echo_response(message)
        else:
            response = make_error("E_NO_ROUTE", f"Unsupported intent for worker: {intent}", message_id)
            redis_append_event(rdb, message_id, "worker_error", {"node_id": NODE_ID, "code": "E_NO_ROUTE"})
            publish_log(channel, message_id, "worker_error", {"node_id": NODE_ID, "code": "E_NO_ROUTE"})
    except Exception as exc:
        response = make_error(
            "E_NODE_UNAVAILABLE",
            f"LLM processing failed: {type(exc).__name__}",
            message_id,
            retryable=True,
            details={"error": str(exc), "ollama_base_url": OLLAMA_BASE_URL},
        )
        redis_append_event(rdb, message_id, "worker_error", {"node_id": NODE_ID, "code": "E_NODE_UNAVAILABLE"})
        publish_log(channel, message_id, "worker_error", {"node_id": NODE_ID, "code": "E_NODE_UNAVAILABLE"})

    rdb.set(cached_response_key, json.dumps(response))
    redis_append_event(rdb, message_id, "worker_completed", {"node_id": NODE_ID, "attempt": attempt, "intent": response.get("intent")})
    publish_log(channel, message_id, "worker_completed", {"node_id": NODE_ID, "attempt": attempt, "intent": response.get("intent")})
    send_result(message_id, response, attempt=attempt, duplicate=False, dead_lettered=False)


def main() -> None:
    print(f"{NODE_ID} worker starting")
    print(f"ollama base url: {OLLAMA_BASE_URL}")
    _ = redis_client()

    conn = rabbit_connection()
    ch = conn.channel()
    ensure_topology(ch)
    ch.basic_qos(prefetch_count=1)

    def callback(channel: Any, method: Any, properties: Any, body: bytes) -> None:
        try:
            process_delivery(channel, body)
            channel.basic_ack(delivery_tag=method.delivery_tag)
        except Exception as exc:  # pragma: no cover - operational path
            print(f"worker exception: {type(exc).__name__}: {exc}")
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

    ch.basic_consume(queue=QUEUE_ECHO, on_message_callback=callback, auto_ack=False)
    print(f"{NODE_ID} consuming queue={QUEUE_ECHO}")
    ch.start_consuming()


if __name__ == "__main__":
    main()
