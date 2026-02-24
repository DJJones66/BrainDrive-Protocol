from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib import error, request

import pika
import redis

PROTOCOL_VERSION = "0.1"

E_BAD_MESSAGE = "E_BAD_MESSAGE"
E_UNSUPPORTED_PROTOCOL = "E_UNSUPPORTED_PROTOCOL"
E_NO_ROUTE = "E_NO_ROUTE"
E_REQUIRED_EXTENSION_MISSING = "E_REQUIRED_EXTENSION_MISSING"
E_NODE_UNAVAILABLE = "E_NODE_UNAVAILABLE"
E_NODE_TIMEOUT = "E_NODE_TIMEOUT"
E_NODE_ERROR = "E_NODE_ERROR"
E_INTERNAL = "E_INTERNAL"

EX_CAPABILITY = "bdp.capability"
EX_LOG = "bdp.log"
EX_DLQ = "bdp.dlq"

QUEUE_ECHO = "q.echo"
QUEUE_LOG = "q.log_event"
QUEUE_ECHO_DLQ = "q.echo.dlq"


def env(name: str, default: str) -> str:
    return str(os.getenv(name, default))


def new_uuid() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dumps(data: Dict[str, Any]) -> str:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=True)


def json_loads(raw: bytes | str) -> Dict[str, Any]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("JSON payload must decode to an object")
    return parsed


def make_error(
    code: str,
    message: str,
    parent_message_id: Optional[str],
    retryable: bool = False,
    details: Optional[Dict[str, Any]] = None,
    protocol_version: str = PROTOCOL_VERSION,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "protocol_version": protocol_version,
        "message_id": new_uuid(),
        "intent": "error",
        "payload": {
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "details": details or {},
            }
        },
        "extensions": {},
    }
    if parent_message_id:
        ensure_trace(body, parent_message_id, hop=None)
    return body


def validate_core(message: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(message, dict):
        return make_error(E_BAD_MESSAGE, "Message must be an object", None)

    message_id = message.get("message_id")
    for field in ("protocol_version", "message_id", "intent", "payload"):
        if field not in message:
            return make_error(E_BAD_MESSAGE, f"Missing required field: {field}", message_id)

    if not isinstance(message["protocol_version"], str):
        return make_error(E_BAD_MESSAGE, "protocol_version must be string", message_id)
    if not isinstance(message["message_id"], str):
        return make_error(E_BAD_MESSAGE, "message_id must be string", message_id)
    if not isinstance(message["intent"], str):
        return make_error(E_BAD_MESSAGE, "intent must be string", message_id)
    if not isinstance(message["payload"], dict):
        return make_error(E_BAD_MESSAGE, "payload must be object", message_id)
    if "extensions" in message and message["extensions"] is not None and not isinstance(message["extensions"], dict):
        return make_error(E_BAD_MESSAGE, "extensions must be object if present", message_id)
    return None


def looks_like_bdp(message: Any) -> bool:
    if not isinstance(message, dict):
        return False
    return (
        isinstance(message.get("protocol_version"), str)
        and isinstance(message.get("message_id"), str)
        and isinstance(message.get("intent"), str)
        and isinstance(message.get("payload"), dict)
        and (
            "extensions" not in message
            or message.get("extensions") is None
            or isinstance(message.get("extensions"), dict)
        )
    )


def ensure_extensions(message: Dict[str, Any]) -> Dict[str, Any]:
    if "extensions" not in message or message["extensions"] is None:
        message["extensions"] = {}
    return message


def ensure_trace(message: Dict[str, Any], parent_message_id: Optional[str], hop: Optional[str]) -> Dict[str, Any]:
    ensure_extensions(message)
    trace = message["extensions"].setdefault(
        "trace",
        {
            "parent_message_id": parent_message_id or message.get("message_id"),
            "depth": 0,
            "path": [],
        },
    )
    trace.setdefault("parent_message_id", parent_message_id or message.get("message_id"))
    trace.setdefault("depth", 0)
    trace.setdefault("path", [])
    trace["depth"] = int(trace["depth"]) + 1
    if hop:
        trace["path"].append(hop)
    return message


def data_dir() -> Path:
    base = Path(env("BDP_DATA_DIR", "/workspace/data/logs"))
    base.mkdir(parents=True, exist_ok=True)
    return base


def append_jsonl(filename: str, entry: Dict[str, Any]) -> None:
    path = data_dir() / filename
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def redis_client(max_wait_sec: float = 30.0) -> redis.Redis:
    host = env("REDIS_HOST", "redis")
    port = int(env("REDIS_PORT", "6379"))
    deadline = time.time() + max_wait_sec
    last_error: Optional[Exception] = None

    while time.time() < deadline:
        try:
            client = redis.Redis(host=host, port=port, decode_responses=True)
            client.ping()
            return client
        except Exception as exc:  # pragma: no cover - startup retry
            last_error = exc
            time.sleep(1.0)

    raise RuntimeError(f"Failed to connect to Redis at {host}:{port}") from last_error


def rabbit_connection(max_wait_sec: float = 30.0) -> pika.BlockingConnection:
    host = env("RABBITMQ_HOST", "rabbitmq")
    port = int(env("RABBITMQ_PORT", "5672"))
    user = env("RABBITMQ_USER", "bdp")
    password = env("RABBITMQ_PASS", "bdp")
    creds = pika.PlainCredentials(user, password)
    params = pika.ConnectionParameters(host=host, port=port, credentials=creds, heartbeat=30, blocked_connection_timeout=30)

    deadline = time.time() + max_wait_sec
    last_error: Optional[Exception] = None
    while time.time() < deadline:
        try:
            return pika.BlockingConnection(params)
        except Exception as exc:  # pragma: no cover - startup retry
            last_error = exc
            time.sleep(1.0)
    raise RuntimeError(f"Failed to connect to RabbitMQ at {host}:{port}") from last_error


def ensure_topology(channel: pika.adapters.blocking_connection.BlockingChannel) -> None:
    channel.exchange_declare(exchange=EX_CAPABILITY, exchange_type="direct", durable=True)
    channel.exchange_declare(exchange=EX_LOG, exchange_type="fanout", durable=True)
    channel.exchange_declare(exchange=EX_DLQ, exchange_type="direct", durable=True)

    channel.queue_declare(queue=QUEUE_ECHO, durable=True)
    channel.queue_bind(queue=QUEUE_ECHO, exchange=EX_CAPABILITY, routing_key="echo")

    channel.queue_declare(queue=QUEUE_LOG, durable=True)
    channel.queue_bind(queue=QUEUE_LOG, exchange=EX_LOG, routing_key="")

    channel.queue_declare(queue=QUEUE_ECHO_DLQ, durable=True)
    channel.queue_bind(queue=QUEUE_ECHO_DLQ, exchange=EX_DLQ, routing_key="echo")


def publish_json(channel: pika.adapters.blocking_connection.BlockingChannel, exchange: str, routing_key: str, body: Dict[str, Any]) -> None:
    channel.basic_publish(
        exchange=exchange,
        routing_key=routing_key,
        body=json_dumps(body),
        properties=pika.BasicProperties(content_type="application/json", delivery_mode=2),
    )


def post_json(url: str, payload: Dict[str, Any], timeout_sec: float = 5.0) -> Dict[str, Any]:
    req = request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout_sec) as resp:
            body = resp.read().decode("utf-8")
    except error.URLError as exc:
        raise RuntimeError(f"HTTP POST failed for {url}: {exc}") from exc
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise RuntimeError(f"HTTP response from {url} was not a JSON object")
    return parsed


def redis_set_status(rdb: redis.Redis, message_id: str, state: str, extra: Optional[Dict[str, Any]] = None) -> None:
    payload: Dict[str, str] = {
        "message_id": message_id,
        "state": state,
        "updated_at": now_iso(),
    }
    if extra:
        for key, value in extra.items():
            payload[key] = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
    rdb.hset(f"bdp:status:{message_id}", mapping=payload)


def redis_get_status(rdb: redis.Redis, message_id: str) -> Dict[str, Any]:
    raw = rdb.hgetall(f"bdp:status:{message_id}")
    out: Dict[str, Any] = {}
    for key, value in raw.items():
        if key in {"request", "response", "details", "error", "meta"}:
            try:
                out[key] = json.loads(value)
                continue
            except Exception:
                pass
        out[key] = value
    return out


def redis_append_event(rdb: redis.Redis, message_id: str, event: str, details: Optional[Dict[str, Any]] = None) -> None:
    entry = {
        "ts": now_iso(),
        "event": event,
        "message_id": message_id,
        "details": details or {},
    }
    rdb.rpush(f"bdp:events:{message_id}", json.dumps(entry))
    append_jsonl("router-events.jsonl", entry)


def redis_get_events(rdb: redis.Redis, message_id: str) -> list[Dict[str, Any]]:
    rows = rdb.lrange(f"bdp:events:{message_id}", 0, -1)
    out: list[Dict[str, Any]] = []
    for row in rows:
        try:
            parsed = json.loads(row)
            if isinstance(parsed, dict):
                out.append(parsed)
        except Exception:
            continue
    return out
