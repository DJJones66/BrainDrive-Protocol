# Braindrive Protocal (BDP)

Version: `0.1` (Foundational Specification)  
Status: Draft

This document defines the **Braindrive Protocal** as the canonical name for the protocol previously referred to as ANCP in concept drafts.

---

## 1. Abstract

Braindrive Protocal (BDP) defines a minimal, extensible message contract for modular node-based systems.

BDP enables:

- Interchangeable nodes
- Capability-based routing
- Optional identity and permissions
- Version negotiation
- Forward and backward compatibility
- Additive feature evolution without breaking core behavior

The only mandatory system components are:

- A message transport
- A routing mechanism

All other behavior (identity, permissions, memory, planning, execution, arbitration, observability) is optional and layered through node capabilities and protocol extensions.

---

## 2. Design Principles

### 2.1 Minimal Core

The core contract is intentionally small and stable.

### 2.2 Optional Extensions

Advanced behavior is represented under `extensions`, not by mutating required core fields.

### 2.3 Capability-Based Interoperability

Nodes declare capabilities. The router uses declared capability and compatibility, not hardcoded node meaning.

### 2.4 Versioned Evolution

Protocol and node versions evolve independently while preserving compatibility through additive changes and adapters.

### 2.5 Non-Breaking Growth

New behavior SHOULD be introduced as additive extensions instead of mutating required core fields.

---

## 3. Core Message Contract

### 3.1 Required Message Structure

Every BDP message MUST contain:

```json
{
  "protocol_version": "0.1",
  "message_id": "uuid",
  "intent": "string",
  "payload": {}
}
```

### 3.2 Field Definitions

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `protocol_version` | string | Yes | Protocol version used by sender |
| `message_id` | string | Yes | Unique message identifier |
| `intent` | string | Yes | Requested action |
| `payload` | object | Yes | Data associated with the intent |

This is the only invariant required contract in BDP v0.1.

### 3.3 Message Types

- Request: any valid BDP message with actionable `intent`
- Response: any valid BDP message returning data/results
- Error: valid BDP message where `intent` is `"error"` and payload follows the error schema

---

## 4. Optional Extensions

All optional features MUST live under `extensions`.

```json
{
  "extensions": {}
}
```

If `extensions` is absent, the system operates in minimal mode.

### 4.1 Identity Extension

```json
{
  "extensions": {
    "identity": {
      "actor_id": "user.dave",
      "actor_type": "human | agent | system",
      "roles": ["admin"],
      "session_id": "optional"
    }
  }
}
```

Rules:

- If absent, system may run single-owner mode
- Nodes may require identity via node descriptor `requires`
- Router enforces required extensions when configured

### 4.2 Permissions Extension

```json
{
  "extensions": {
    "permissions": {
      "scope": "projectA",
      "requested_capability": "execute_task",
      "elevation_required": false
    }
  }
}
```

If absent, the system assumes open execution.

### 4.3 Confidence Extension

```json
{
  "extensions": {
    "confidence": {
      "score": 0.87,
      "basis": "self-evaluated"
    }
  }
}
```

Used for weighted routing, arbitration, and merge decisions.

### 4.4 Cost Extension

```json
{
  "extensions": {
    "cost": {
      "estimated_compute": "medium",
      "estimated_latency_ms": 450
    }
  }
}
```

### 4.5 Streaming Extension

```json
{
  "extensions": {
    "streaming": {
      "enabled": true,
      "chunk_index": 3,
      "is_final": false
    }
  }
}
```

### 4.6 Trace Extension

```json
{
  "extensions": {
    "trace": {
      "parent_message_id": "uuid",
      "depth": 2,
      "path": ["interface.cli", "router.core", "planner.alpha"]
    }
  }
}
```

### 4.7 Feature Flags Extension

```json
{
  "extensions": {
    "features": {
      "supports_identity": true,
      "supports_permissions": false,
      "supports_confidence": true
    }
  }
}
```

Unknown extensions MUST be ignored (forward-compatibility rule).

---

## 5. Node Registration Spec

Each node MUST register a descriptor with the router:

```json
{
  "node_id": "execution.ollama",
  "node_version": "1.2.0",
  "supported_protocol_versions": ["0.1"],
  "capabilities": ["execute_task", "summarize"],
  "requires": [],
  "optional_features_supported": ["identity", "confidence", "streaming"],
  "priority": 100
}
```

Field notes:

- `node_id`: globally unique node identifier
- `supported_protocol_versions`: versions this node can process
- `capabilities`: operations this node can handle
- `requires`: extensions required for routing to this node
- `priority`: optional deterministic routing weight (higher preferred)

---

## 6. Router Behavior (Normative)

### 6.1 Minimal Routing Sequence

1. Validate core message structure and types
2. Find protocol-compatible nodes by `protocol_version`
3. Match `intent` to capability (default rule: `intent == capability`)
4. Enforce node `requires` extensions
5. Optionally enforce permissions policy
6. Select best node deterministically
7. Optionally append trace metadata
8. Dispatch and await response
9. Validate response shape (best effort)
10. Return response as-is (or standardized error)

### 6.2 Selection Rule

When more than one eligible node exists, router SHOULD select deterministically:

- Higher `priority` first
- Then newer `node_version`
- Then stable tie-break (`node_id`)

### 6.3 Optional Planner Fallback

If no route exists (`E_NO_ROUTE`), router MAY invoke a planner node with `plan_route` capability to generate a routable BDP message.

### 6.4 Router Invariants

- Router is structural, not semantic
- Router MUST validate shape and compatibility before dispatch
- Router MUST NOT rewrite required core fields (`protocol_version`, `message_id`, `intent`, `payload`)
- Router SHOULD forward the selected request unchanged except for optional extension-level metadata (for example trace)

### 6.5 Formal Router Pseudocode (v0.1)

```text
ROUTE(message):
  validate core fields and field types
  find nodes supporting message.protocol_version
  if none: try adapter path else E_UNSUPPORTED_PROTOCOL

  capability_needed = message.intent
  filter nodes where capability_needed in node.capabilities
  if none: E_NO_ROUTE

  enforce node.requires against message.extensions
  if none eligible: E_REQUIRED_EXTENSION_MISSING

  optionally run permission check -> E_PERMISSION_DENIED
  select best node deterministically (priority, node_version, node_id)

  optionally append trace metadata
  response = transport.send(selected_node, message, timeout)
  if timeout: E_NODE_TIMEOUT
  if invalid response shape: E_NODE_ERROR
  return response
```

### 6.6 Optional Sidecar Patterns

Logger sidecar (`obs.logger`):

- Router MAY emit secondary `log_event` messages for route decisions and completion
- Logger failure MUST NOT block main routing (best effort only)

Planner fallback (`planner.alpha`):

- On `E_NO_ROUTE`, router MAY send `plan_route` request to planner
- Planner returns a new routable BDP message
- Router routes the returned message as a normal request

---

## 7. Standard Error Model

### 7.1 Error Message Shape

```json
{
  "protocol_version": "0.1",
  "message_id": "uuid",
  "intent": "error",
  "payload": {
    "error": {
      "code": "string",
      "message": "string",
      "retryable": false,
      "details": {}
    }
  },
  "extensions": {
    "trace": {
      "parent_message_id": "uuid"
    }
  }
}
```

### 7.2 Error Codes

| Code | Meaning | Retryable |
| --- | --- | --- |
| `E_BAD_MESSAGE` | Missing or invalid core fields | false |
| `E_UNSUPPORTED_PROTOCOL` | No node supports the protocol version | false |
| `E_NO_ROUTE` | No node matches requested capability | false |
| `E_REQUIRED_EXTENSION_MISSING` | Node requires extension not present | false |
| `E_PERMISSION_DENIED` | Permission policy denied execution | false |
| `E_NODE_UNAVAILABLE` | Node down or unavailable | true |
| `E_NODE_TIMEOUT` | Node did not respond in time | true |
| `E_NODE_ERROR` | Node failed or returned invalid response | depends |
| `E_ADAPTER_NOT_FOUND` | Required protocol adapter missing | false |
| `E_INTERNAL` | Router/internal unexpected exception | maybe |

### 7.3 Error Rules

- Router MUST return exactly one terminal error when routing fails
- Router SHOULD include `extensions.trace.parent_message_id`
- Router MUST NOT leak secrets in `details`
- If a node returns `intent: "error"`, default behavior is pass-through (policy may sanitize/wrap)

---

## 8. Capability Negotiation and Adapters

Negotiation order:

1. Match protocol version
2. Match capability
3. Verify required extensions
4. Route

If protocol mismatch occurs:

- Reject with `E_UNSUPPORTED_PROTOCOL`, or
- Route through adapter node that translates between versions/schemas/extensions

Example:

```text
protocol 0.2 -> adapter -> protocol 0.1
```

---

## 9. Operating Modes

### Mode 0: Minimal

- No identity
- No permissions
- No confidence arbitration
- Pure capability routing

### Mode 1: Identity Enabled

- Identity extension processed
- Identity may be present without permission enforcement

### Mode 2: Permission Enforcement

- Identity required for protected routes
- Permission checks enforced

### Mode 3: Adaptive Arbitration

- Confidence/cost aware selection enabled
- Parallel execution and merge policies allowed

### 9.1 Identity Simulation Examples

If target node requires `identity`:

- Message without `extensions.identity` MUST fail with `E_REQUIRED_EXTENSION_MISSING`
- Message with valid `extensions.identity` SHOULD route normally

---

## 10. Evolution and Compatibility Rules

### 10.1 Node Evolution

Node version MUST increment when:

- Input/output schema changes
- Required extensions change
- Behavior changes materially

Nodes MAY support multiple protocol versions concurrently.

### 10.2 Backward Compatibility

No new feature may:

- Remove required core fields
- Change semantics of required core fields in breaking ways

New features MUST be additive under `extensions`.

### 10.3 Forward Compatibility

Nodes MUST ignore unknown extensions.

---

## 11. Core System Requirements

The system MUST provide:

1. Message transport layer
2. Router capable of protocol/capability matching
3. Node registry

The system does NOT require:

- Identity
- Permissions
- Memory
- Execution engines
- Business logic

These are pluggable protocol participants.

---

## 12. Future Expansion and Invariant Model

Future features may include:

- Negotiated contracts
- Economic cost routing
- Distributed node federation
- Multi-node voting
- Cryptographic message signing

Protocol invariant:

> Structured intent-bearing messages flowing between capability-declared nodes.

---

## 13. Minimal Valid BDP Message

```json
{
  "protocol_version": "0.1",
  "message_id": "abc123",
  "intent": "echo",
  "payload": {
    "text": "Hello"
  }
}
```

Everything else is layered on top of this minimal contract.

---

## 14. Summary

Braindrive Protocal provides:

- A stable core for node-to-node messaging
- Capability-driven interoperability
- Optional identity/permission/security layers
- Deterministic routing with extensible arbitration
- Long-term evolution without systemic breakage
