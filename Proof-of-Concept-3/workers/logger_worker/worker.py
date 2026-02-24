from __future__ import annotations

import json
import os
from typing import Any

from shared.bdp import QUEUE_LOG, append_jsonl, ensure_topology, json_loads, rabbit_connection

WORKER_NAME = os.getenv("LOGGER_WORKER_NAME", "obs.logger.worker")


def main() -> None:
    print(f"{WORKER_NAME} starting")
    conn = rabbit_connection()
    ch = conn.channel()
    ensure_topology(ch)
    ch.basic_qos(prefetch_count=20)

    def callback(channel: Any, method: Any, properties: Any, body: bytes) -> None:
        try:
            message = json_loads(body)
            append_jsonl(
                "logger-events.jsonl",
                {
                    "worker": WORKER_NAME,
                    "event": message.get("event"),
                    "message_id": message.get("message_id"),
                    "details": message.get("details", {}),
                },
            )
            channel.basic_ack(delivery_tag=method.delivery_tag)
        except Exception as exc:  # pragma: no cover - operational
            append_jsonl(
                "logger-events.jsonl",
                {
                    "worker": WORKER_NAME,
                    "event": "logger_error",
                    "details": {"error": f"{type(exc).__name__}: {exc}", "raw": body.decode('utf-8', errors='replace')},
                },
            )
            channel.basic_ack(delivery_tag=method.delivery_tag)

    ch.basic_consume(queue=QUEUE_LOG, on_message_callback=callback, auto_ack=False)
    print(f"{WORKER_NAME} consuming queue={QUEUE_LOG}")
    ch.start_consuming()


if __name__ == "__main__":
    main()
