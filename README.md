# Concepts: BrainDrive Protocol Primer

This file is a plain-language introduction to the BrainDrive Protocol (BDP), based on:

- `/home/hacker/Projects/BrainDrive-Protocal/docs/protocal.md`

If you are new to this project, start here.

## What is BrainDrive Protocol?

BrainDrive Protocol is a shared message format and routing model for modular systems.

In simple terms:

- A component sends a request as a standard message.
- A router decides which node can handle that request.
- The selected node returns a standard response.

Because every node follows the same contract, nodes can be swapped or added without redesigning the whole system.

Think of BDP as the shared language that allows concepts to interoperate.

## Why this exists

Without a protocol, each service integration becomes custom and fragile.

With BDP:

- Components are interchangeable.
- Routing is based on declared capabilities (not hardcoded assumptions).
- Optional features can be added without breaking old behavior.
- Version changes can be handled in a controlled, backward-compatible way.

## The core message (the one thing that must stay stable)

Every BDP message must include only four required fields:

```json
{
  "protocol_version": "0.1",
  "message_id": "uuid",
  "intent": "string",
  "payload": {}
}
```

Meaning:

- `protocol_version`: which protocol version this message follows.
- `message_id`: unique id for tracing and idempotency.
- `intent`: the requested action.
- `payload`: the data for that action.

Everything else is optional and goes under `extensions`.

## Optional extensions

Advanced features live in `extensions`, for example:

- identity (`extensions.identity`)
- permissions/authz (`extensions.permissions`)
- confidence (`extensions.confidence`)
- streaming metadata (`extensions.streaming`)
- trace metadata (`extensions.trace`)

Important compatibility rule:

- Unknown extensions should be ignored, not treated as fatal.

This allows forward-compatible growth.

## Main parts of a BDP system

## 1) Router

The router is structural, not semantic. It does not "think" about user meaning; it only:

- validates message shape
- finds protocol-compatible nodes
- matches `intent` to capability
- enforces required extensions
- selects the best node deterministically
- dispatches and returns response/error

## 2) Nodes

Nodes declare what they can do (capabilities), for example:

- `chat.general`
- `workflow.plan.generate`
- `md.library.create_note`

Nodes register descriptors with the router including:

- `node_id`
- protocol support
- capabilities
- requirements (`requires`)
- priority/version data

## 3) Transport

Any transport can be used (HTTP, queue, etc.) as long as the message contract stays valid.

## Request lifecycle (high level)

1. A client sends a BDP request.
2. Router validates the envelope.
3. Router finds capable + eligible node(s).
4. Router selects one deterministically.
5. Router dispatches request.
6. Node returns a BDP response or BDP error.
7. Caller receives standardized output.

## Error model

Errors are also BDP messages (`intent: "error"`) with structured error payloads.

Common examples:

- bad message shape
- unsupported protocol
- no route for intent
- missing required extension
- node unavailable/timeout


## Quick glossary

- Protocol: common rules for message shape and behavior.
- Intent: the action being requested.
- Capability: what a node can handle.
- Node: a service/component that handles one or more capabilities.
- Router: component that selects and dispatches to the right node.
- Extension: optional metadata that adds advanced behavior.
