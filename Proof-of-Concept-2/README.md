# BrainDrive Protocol PoC 2

## Why this PoC exists

PoC 2 moves from a single-process simulation (PoC 1) to a **real distributed setup**.

In this PoC, each responsibility is its own service:

- router
- echo node
- planner node
- adapter node
- logger sidecar

All of them communicate over HTTP. This makes behavior closer to how a real modular BrainDrive deployment works.

## What PoC 2 demonstrates (in plain language)

1. **Distributed routing over network transport**
   - The router receives a request and calls another service to fulfill it.
2. **Deterministic capability selection**
   - Router picks the best matching node for a capability.
3. **Identity requirement enforcement**
   - Requests missing identity are rejected in a standard way.
4. **Planner fallback**
   - If no direct route exists (`say_hi`), planner proposes a routable message.
5. **Protocol adapter fallback**
   - A `0.2` request can be adapted to `0.1` and still succeed.
6. **Standardized unavailable-node handling**
   - If a node is down, router returns `E_NODE_UNAVAILABLE` (retryable).
7. **Sidecar observability**
   - Router events and logger events are persisted separately.

## Why this is important

This PoC proves BDP still works when components are physically separated.

For non-technical reviewers, this is the point where BrainDrive stops being a local toy and starts behaving like a resilient service architecture.

## Architecture at a glance

- `router` (Python)
  - Public API on `http://<POC2_HOST>:<POC2_PORT>` (default `http://localhost:8080`)
  - Endpoint: `POST /route`, `GET /health`
- `node-echo` (Python)
  - Handles `echo`
- `node-planner` (Node.js)
  - Handles planner fallback logic
- `node-adapter` (Python)
  - Adapts protocol `0.2` -> `0.1`
- `node-logger` (Python)
  - Receives log events; writes JSONL

## Run

```bash
cd /home/hacker/Projects/BrainDrive-Protocal/Proof-of-Concept-2
docker compose up -d
```

Run all validation scenarios:

```bash
./scripts/demo.sh
```

Stop:

```bash
docker compose down
```

Host/IP values for scripts and compose port mapping are centralized in:

- `/home/hacker/Projects/BrainDrive-Protocal/Proof-of-Concept-2/.env`

## What the demo script validates

The script covers six scenarios:

1. `echo` with identity -> success.
2. `echo` without identity -> `E_REQUIRED_EXTENSION_MISSING`.
3. `say_hi` -> planner fallback -> echo response.
4. protocol `0.2` -> adapter -> success.
5. protocol `0.3` with no adapter -> `E_ADAPTER_NOT_FOUND`.
6. echo node intentionally stopped -> `E_NODE_UNAVAILABLE`.

If everything works, you will see:

- `All PoC2 demo scenarios passed.`

## Manual quick test

Health check:

```bash
source .env
curl -s "http://${POC2_HOST}:${POC2_PORT}/health" | jq
```

Route request:

```bash
curl -s "http://${POC2_HOST}:${POC2_PORT}/route" \
  -H 'Content-Type: application/json' \
  -d '{
    "protocol_version":"0.1",
    "message_id":"manual-1",
    "intent":"echo",
    "payload":{"text":"hello"},
    "extensions":{"identity":{"actor_id":"user.cli","actor_type":"human","roles":["admin"]}}
  }' | jq
```

## Persistence (what is saved)

All services bind-mount this directory into `/workspace`, so logs persist on your host.

Important files:

- `data/router-events.jsonl`
- `data/logger-events.jsonl`

## Environment configuration

Configured via `.env`:

- `POC2_HOST`
- `POC2_PORT`
- `ROUTER_URL` (optional full script endpoint override)

## What this PoC does **not** prove yet

- No durable async queueing (requests are still request/response style).
- No replay API for full event history per message.
- No dead-letter queue processing.
- No live token streaming from LLMs.

Those appear in PoC 3 and PoC 4.
