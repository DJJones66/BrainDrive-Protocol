# BrainDrive Protocol PoC 4

## Why this PoC exists

PoC 4 adds the user-facing experience: **live streaming LLM output** with a browser UI, while still keeping reliability through fallback to PoC 3 when needed.

In simple terms:

- Fast path: stream tokens live for short/normal prompts.
- Safe path: send long/high-risk prompts to durable async processing.

This PoC demonstrates both speed and reliability together.

## What PoC 4 demonstrates (in plain language)

1. **Live token streaming**
   - Endpoint: `POST /stream`
   - Server-Sent Events (SSE): `meta`, `token`, `async_queued`, `done`, `error`
2. **Non-stream completion path**
   - Endpoint: `POST /complete`
3. **Browser chat interface**
   - Endpoint: `GET /ui`
4. **Logical node routing for prompt style**
   - `general` node profile (default: `ministral-3:8b`)
   - `builder` node profile (default: `qwen3:8b`)
5. **Model override controls**
   - UI dropdowns, CLI flags, or prompt directives (`/model:<name>`, `/node:<name>`)
6. **Generation control knobs**
   - `max_tokens` (maps to Ollama `num_predict`)
   - `stop` sequences
7. **Automatic durable fallback to PoC 3**
   - Long prompts are queued through PoC 3 async router instead of staying on a live stream path.

## Why this is important

A production-grade protocol needs two properties at the same time:

- **Responsiveness** for interactive use.
- **Durability** for longer or riskier requests.

PoC 4 proves BDP can support both with one protocol shape and clear route metadata.

## Architecture at a glance

- PoC4 `bdp-stream-router` (Python)
  - Host endpoint: `http://<POC4_HOST>:<POC4_PORT>` (default `http://localhost:8080`)
  - UI + stream + complete APIs
  - Calls Ollama for direct generation
  - Can forward to PoC3 async queue when fallback triggers
- External Ollama
  - Configured by `.env` value `OLLAMA_BASE_URL`
  - Default: `http://host.docker.internal:11434`
- Optional PoC3 backend (for fallback)
  - Configured by `.env` values `ASYNC_FALLBACK_ROUTE_URL` and `ASYNC_FALLBACK_STATUS_BASE`
  - Default: `http://host.docker.internal:8082`

## Run

Start PoC4:

```bash
cd /home/hacker/Projects/BrainDrive-Protocal/Proof-of-Concept-4
docker compose up -d
```

Configure host/IP settings (single place):

```bash
cd /home/hacker/Projects/BrainDrive-Protocal/Proof-of-Concept-4
sed -n '1,200p' .env
```

Start PoC3 too (required if you want async fallback):

```bash
cd /home/hacker/Projects/BrainDrive-Protocal/Proof-of-Concept-3
docker compose up -d
```

## LAN access (non-localhost)

Open from your local network:

- `http://<server-ip>:<POC4_PORT>/ui`

Typical setup:

- set `POC4_HOST=<server-ip>` in `.env`
- run `docker compose up -d`
- open `http://<server-ip>:<POC4_PORT>/ui`

## Quick checks

```bash
source .env
ROUTER_BASE_URL="${ROUTER_BASE_URL:-http://${POC4_HOST}:${POC4_PORT}}"
curl -s "${ROUTER_BASE_URL}/health" | jq
curl -s "${ROUTER_BASE_URL}/nodes" | jq
curl -s "${ROUTER_BASE_URL}/models" | jq '.models[:10]'
```

## Demo script

```bash
cd /home/hacker/Projects/BrainDrive-Protocal/Proof-of-Concept-4
./scripts/demo.sh
```

The script validates:

- health
- node/model discovery
- direct stream route
- directive-based routing
- complete endpoint behavior
- forced async fallback behavior

## CLI usage

Streaming examples:

```bash
source .env
ROUTER_BASE_URL="${ROUTER_BASE_URL:-http://${POC4_HOST}:${POC4_PORT}}"
python3 scripts/chat_cli.py --router-base "${ROUTER_BASE_URL}" "What is BrainDrive Protocol in 2 bullets?"
python3 scripts/chat_cli.py --router-base "${ROUTER_BASE_URL}" --node builder --model qwen3:8b "Design a streaming router skeleton"
python3 scripts/chat_cli.py --router-base "${ROUTER_BASE_URL}" --max-tokens 300 --stop "</s>" "Keep this short."
```

Non-stream example:

```bash
python3 scripts/chat_cli.py --router-base "${ROUTER_BASE_URL}" --complete --model llama3.1:8b "One sentence summary"
```

Force async fallback:

```bash
python3 scripts/chat_cli.py --router-base "${ROUTER_BASE_URL}" --complete --force-async "Queue this request"
```

## Prompt directives

You can include routing/model directives inside the prompt text itself:

- `/node:builder`
- `/model:llama3.1:8b`

Example:

```text
/node:builder /model:qwen3:8b Build a tiny streaming plan.
```

## Understanding streaming stop behavior

There are two ways a stream ends:

1. **Model-side terminal stop**
   - Reason in event metadata is usually `stop` or `length`.
   - Controlled by `max_tokens` and optional `stop` sequences.
2. **User-side stop**
   - Clicking **Stop Stream** aborts the browser request immediately.

The Events panel should show a `done` event when model-side termination occurs.

## Async fallback behavior

Fallback is controlled by environment settings:

- `ASYNC_FALLBACK_ENABLED=true`
- `ASYNC_FALLBACK_MIN_CHARS=700` (default threshold)
- `ASYNC_FALLBACK_ROUTE_URL` (default `http://host.docker.internal:8082/route_async`)
- `ASYNC_FALLBACK_STATUS_BASE` (default `http://host.docker.internal:8082`)

When fallback triggers, PoC4 returns queue metadata (`status_url`, `replay_url`) so clients can track completion through PoC3.

## API summary

- `GET /health`
- `GET /nodes`
- `GET /models`
- `GET /api`
- `GET /ui`
- `POST /stream`
- `POST /complete`

Minimal request example:

```json
{
  "protocol_version": "0.1",
  "message_id": "uuid",
  "intent": "chat",
  "payload": {
    "text": "hello"
  },
  "extensions": {
    "llm": {
      "node": "general",
      "model": "ministral-3:8b",
      "max_tokens": 300,
      "stop": ["</s>", "<|eot_id|>"]
    }
  }
}
```

## Persistence (what is saved)

- `data/events.jsonl`

## Environment configuration

Primary file:

- `/home/hacker/Projects/BrainDrive-Protocal/Proof-of-Concept-4/.env`

Variables used by compose/scripts:

- `POC4_HOST`
- `POC4_PORT`
- `ROUTER_BASE_URL` (scripts)
- `OLLAMA_BASE_URL`
- `OLLAMA_DEFAULT_MAX_TOKENS`
- `OLLAMA_DEFAULT_STOP`
- `ASYNC_FALLBACK_ENABLED`
- `ASYNC_FALLBACK_MIN_CHARS`
- `ASYNC_FALLBACK_ROUTE_URL`
- `ASYNC_FALLBACK_STATUS_BASE`

## What this PoC does **not** prove yet

- No auth/permission policy enforcement beyond identity field presence.
- No multi-node voting or confidence-based arbitration.
- No cryptographic signing.

Those would belong to later protocol maturity stages.
