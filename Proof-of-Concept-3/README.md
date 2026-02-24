# BrainDrive Protocol PoC 3

## Why this PoC exists

PoC 3 introduces **durable asynchronous processing**.

In plain terms: if a worker is down, busy, restarted, or fails temporarily, the message should not disappear. It should remain tracked until there is a clear final state.

This PoC is the reliability milestone in the series.

## What PoC 3 demonstrates (in plain language)

1. **Async acceptance model**
   - `POST /route_async` accepts work and returns tracking URLs.
2. **Durable queueing**
   - Messages are placed on RabbitMQ so they survive worker downtime.
3. **Status tracking**
   - `GET /status/{message_id}` shows `queued`, `completed`, `error`, or `dlq`.
4. **Replay / audit trail**
   - `GET /replay/{message_id}` returns request, response, and event timeline.
5. **Retries and dead-letter handling**
   - Forced failures retry up to max attempts, then go to DLQ with standardized error.
6. **Idempotency under duplicate delivery**
   - Duplicate messages do not repeat side effects.
7. **LLM-compatible async intent support**
   - Worker handles both `echo` and `chat` intents.

## Why this is important

Real systems fail in normal operation. Machines restart. Networks jitter. Services time out.

This PoC proves the protocol design can handle those realities without losing accountability.

For stakeholders, this is where confidence in operational safety starts.

## Architecture at a glance

- `router` (Python)
  - Host endpoint: `http://localhost:8082`
  - API: `POST /route_async`, `POST /worker_result`, `GET /status/{id}`, `GET /replay/{id}`, `GET /debug/idempotency/{id}`, `GET /health`
- `worker-echo` (Python)
  - Consumes queue messages
  - Processes `echo` and `chat`
  - Enforces identity
  - Handles retry + DLQ
  - Enforces idempotent completion behavior
  - Calls Ollama using `.env` value `OLLAMA_BASE_URL`
- `worker-logger` (Python)
  - Consumes log events and writes JSONL
- `rabbitmq`
  - Durable messaging backbone
- `redis`
  - Stores status, event history, and idempotency keys

## Run

```bash
cd /home/hacker/Projects/BrainDrive-Protocal/Proof-of-Concept-3
docker compose up -d
./scripts/demo.sh
```

Host/IP and external endpoints are configured in one place:

- `/home/hacker/Projects/BrainDrive-Protocal/Proof-of-Concept-3/.env`

Stop:

```bash
docker compose down
```

## What the demo script validates

The script runs five scenarios:

1. **Normal async route**
   - Message becomes `completed` with expected response.
2. **Duplicate delivery idempotency**
   - Re-sending same message id does not duplicate side effects.
3. **Worker crash/restart recovery**
   - Message stays `queued` while worker is down; completes after restart.
4. **Retries + DLQ**
   - Forced errors retry; after max attempts response becomes `E_NODE_TIMEOUT` and state `dlq`.
5. **Replay trace**
   - Event sequence contains enqueue, worker receive, retries/dead-letter, final result.

If everything works, you will see:

- `All PoC3 scenarios passed.`

## Manual quick test

Queue a message:

```bash
source .env
ROUTER_BASE_URL="${ROUTER_BASE_URL:-http://${POC3_HOST}:${POC3_PORT}}"
curl -s "${ROUTER_BASE_URL}/route_async" \
  -H 'Content-Type: application/json' \
  -d '{
    "protocol_version": "0.1",
    "message_id": "manual-echo-1",
    "intent": "echo",
    "payload": {"text": "hello"},
    "extensions": {
      "identity": {
        "actor_id": "user.cli",
        "actor_type": "human",
        "roles": ["admin"]
      }
    }
  }' | jq
```

Then inspect status:

```bash
curl -s "${ROUTER_BASE_URL}/status/manual-echo-1" | jq
```

## Persistence (what is saved)

Important persisted paths:

- `data/logs/router-events.jsonl`
- `data/logs/logger-events.jsonl`
- `data/redis/*`
- `data/rabbitmq/*`

## Environment configuration

Configured via `.env` and consumed by `docker compose` and scripts:

- `POC3_HOST`
- `POC3_PORT`
- `ROUTER_BASE_URL` (optional full script URL override)
- `OLLAMA_BASE_URL`

## Operational meaning of states

- `queued`: accepted and waiting for worker processing.
- `completed`: successful terminal result.
- `error`: failed terminal result (non-DLQ).
- `dlq`: failed after retry policy exhausted.

## What this PoC does **not** prove yet

- No browser token-by-token streaming UX.
- No direct synchronous chat stream endpoint.
- No advanced multi-node arbitration.

Those are covered in PoC 4 (streaming + UI + async fallback bridge).
