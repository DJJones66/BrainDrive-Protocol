# BrainDrive Protocol PoC 1

## Why this PoC exists

PoC 1 is the smallest possible working demonstration of the BrainDrive Protocol (BDP) ideas.

Think of this PoC as a "single-room simulation":

- One simple request comes in.
- A router validates the request.
- The router picks a node by capability.
- The node replies in protocol format.
- Events are logged for traceability.

No distributed infrastructure is required yet. This is intentional. It proves the core contract before adding network and scale complexity.

## What PoC 1 demonstrates (in plain language)

1. **Core message validation**
   - Messages must include `protocol_version`, `message_id`, `intent`, and `payload`.
2. **Capability routing**
   - `intent: "echo"` routes to the `terminal.echo` node.
3. **Required extension enforcement**
   - `terminal.echo` requires `extensions.identity`.
   - Missing identity returns a standard protocol error.
4. **Planner fallback behavior**
   - `intent: "say_hi"` has no direct handler.
   - Planner transforms it into a routable `echo` message.
5. **Standardized BDP error shape**
   - Failures return protocol-level error messages, not ad-hoc text.
6. **Basic observability**
   - Router decisions and outcomes are written to `data/events.jsonl`.

## Why this is important

This PoC proves that BDP can enforce structure and deterministic behavior before adding advanced features.

For reviewers, this answers:

- "Can the protocol route messages consistently?"
- "Can it fail safely and predictably?"
- "Can optional extensions like identity be enforced?"

If PoC 1 fails, higher-level PoCs are not meaningful.

## What is running

Single container, interactive terminal app:

- `ancp_demo.py` (router + nodes + planner + logger simulation)

## Quick start

```bash
cd /home/hacker/Projects/BrainDrive-Protocal/Proof-of-Concept-1
docker compose run --rm bdp-poc
```

You should see a prompt. Try these commands:

```text
echo Hello world
echo --no-id This should fail
say_hi there
help
quit
```

## How to read the output

- `echo Hello world`
  - Expected: normal echo response with metadata.
- `echo --no-id ...`
  - Expected: `E_REQUIRED_EXTENSION_MISSING`.
- `say_hi there`
  - Expected: planner fallback creates an echo-style reply such as `Hi! there`.

## Persistence (what is saved)

The compose file mounts this folder into the container:

- Host path: `./`
- Container path: `/workspace`

Important persisted file:

- `data/events.jsonl` (routing and completion events)

## Environment configuration

PoC1 is local-only (no network host/IP configuration needed), but it still supports a local `.env` for container settings:

- `/home/hacker/Projects/BrainDrive-Protocal/Proof-of-Concept-1/.env`
- `BDP_DATA_DIR` (default `/workspace/data`)

## What this PoC does **not** prove yet

- No inter-service network communication.
- No distributed queueing.
- No retry or dead-letter behavior.
- No streaming responses.
- No long-running model integration.

Those are introduced in later PoCs.
