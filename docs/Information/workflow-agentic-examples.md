# Workflow Agentic Examples (Router -> Intent Engine -> Orchestrator)

Purpose: show how BrainDrive Protocol can support agentic workflows, what is required to enable them, and why each pattern is useful.

This document is prompt-first so readers can connect user intent to protocol behavior.

---

## 1) What "Agentic Workflow" Means Here

A normal workflow is usually one routed action.

An agentic workflow is a multi-step execution loop where the system can:

- plan steps from a user goal
- call multiple capabilities in sequence
- adapt when a step fails or returns new information
- enforce policy/approval before risky side effects
- persist progress and resume if interrupted

In protocol terms:

- `intent.router.natural-language` maps prompt to an execution plan intent.
- `node.router` routes each step to the correct capability.
- `node.agentic.orchestrator` (or equivalent) manages the multi-step loop.

---

## 2) What Is Needed To Achieve Agentic Workflows

Minimum required components:

1. `intent.router.natural-language`
- classifies prompt into a canonical intent or composed workflow intent
- emits confidence, risk class, reason codes, required extensions

2. `node.router`
- dynamic capability catalog
- deterministic route resolution
- extension gating (`identity`, `authz`, `confirmation`, etc.)

3. `node.agentic.orchestrator` (recommended)
- turns goals into step plans
- executes and re-plans when needed
- manages retries/fallback/branching

4. execution nodes (examples)
- `node.rag`
- `node.cag`
- `node.web.search`
- `node.markdown.library`
- domain nodes (`node.workflow`, `node.build.validator`, etc.)

5. policy/safety nodes
- `node.auth` or `node.permission.policy`
- confirmation/approval handling for mutating and destructive actions

6. observability and durability
- `node.audit.log`
- optional async queue/replay/checkpoint components

Required protocol behavior:

- stable BDP core envelope (`protocol_version`, `message_id`, `intent`, `payload`)
- optional extension support (`session`, `context`, `authz`, `trace`, `agent`)
- fail-closed behavior for protected operations when policy services are down

---

## 3) Agentic Execution Pattern (Generic)

Flow template:

`User Prompt -> Intent Engine -> Router -> Agentic Orchestrator -> (Step Nodes...) -> Router -> Final Response`

Typical loop:

1. Prompt enters gateway.
2. Intent engine emits workflow plan intent with confidence/risk metadata.
3. Router dispatches to orchestrator.
4. Orchestrator decomposes goal into steps.
5. Router dispatches each step to matching capability node.
6. Orchestrator evaluates results and either:
- continues to next step
- retries/fallbacks
- asks for clarification/approval
- ends with final response and artifacts

---

## 4) Agentic Workflow Examples

## 4.1 Internal Research + External Verification + Brief

Prompt example:

`"Use our roadmap docs and current competitor news to produce a 1-page strategic brief with sources."`

Why this is agentic:

- requires multiple knowledge sources
- requires a synthesis step after retrieval
- benefits from iterative checking for completeness

How it would be achieved:

1. Intent engine maps to composed workflow intent.
2. Orchestrator runs:
- `knowledge.rag.query` (internal docs)
- `web.search.query` (fresh external updates)
- `workflow.brief.generate` (merge + summarize)
3. Router dispatches each step by capability.
4. Final response includes brief plus provenance links.

What is needed:

- `node.rag`, `node.web.search`, synthesis node (`node.workflow` or `node.llm.inference`)
- trace + audit capture
- source attribution policy

Why it matters:

- demonstrates non-trivial reasoning over mixed internal and external knowledge
- shows Router + Intent can coordinate multi-step composition, not just single-call chat

---

## 4.2 Conversation Continuity + Plan Revision + Save Artifact

Prompt example:

`"Based on what I committed to in our last two chats and the current project spec, revise my execution plan and append it to note weekly-review."`

Why this is agentic:

- requires context recall plus document retrieval plus mutation
- needs ordering: recall -> retrieve -> generate -> persist

How it would be achieved:

1. Intent engine identifies a multi-step mutate workflow.
2. Orchestrator steps:
- `context.cag.recall`
- `knowledge.rag.query`
- `workflow.plan.generate`
- `md.library.append_note`
3. Router enforces `authz` and confirmation gates for persistence step.

What is needed:

- `node.cag`, `node.rag`, `node.workflow`, `node.markdown.library`
- `identity` + `authz` extensions
- policy/confirmation for mutation

Why it matters:

- shows difference between memory recall and durable storage
- demonstrates safe agentic writing to user-owned artifacts

---

## 4.3 Risky Action With Human Approval Gate

Prompt example:

`"Delete old draft notes and replace them with a condensed final note from this week's findings."`

Why this is agentic:

- includes destructive and mutating operations
- should never auto-execute blindly

How it would be achieved:

1. Intent engine marks risk class `destructive` and requires confirmation.
2. Orchestrator prepares a proposed change set.
3. Router calls policy node for precheck.
4. System pauses at approval state.
5. After explicit approval token, orchestrator executes:
- `md.library.delete_note` (approved targets only)
- `md.library.create_note` or `md.library.append_note`

What is needed:

- policy + approval node
- confirmation token contract
- auditable decision-to-effect linkage

Why it matters:

- demonstrates fail-safe behavior
- proves agentic automation can be controlled, reviewable, and reversible

---

## 4.4 Long-Running Agentic Task (Async + Resume)

Prompt example:

`"Create a migration readiness report across all service docs, validate with current dependency advisories, and checkpoint every stage."`

Why this is agentic:

- may exceed synchronous latency window
- requires durable progression and resumability

How it would be achieved:

1. Intent engine maps to long-running agentic workflow.
2. Router sends to orchestrator in async mode.
3. Orchestrator checkpoints after each phase:
- doc retrieval and clustering
- advisory lookup
- gap scoring
- report synthesis
4. Client polls status or receives streaming progress events.
5. On restart/failure, task resumes from last checkpoint.

What is needed:

- async queue/backbone
- task store + checkpoint manager
- replay/status API

Why it matters:

- demonstrates reliability under real-world latency/failure conditions
- makes agentic workflows operationally safe at scale

---

## 5) Example Intent Plan Payload (Composed)

Example produced by intent engine before orchestrator execution:

```json
{
  "canonical_intent": "workflow.compose.execute",
  "confidence": 0.88,
  "risk_class": "mutate",
  "clarification_required": false,
  "confirmation_required": true,
  "reason_codes": [
    "multi_source_reasoning_required",
    "artifact_persistence_requested"
  ],
  "target_capabilities": [
    "context.cag.recall",
    "knowledge.rag.query",
    "web.search.query",
    "workflow.plan.generate",
    "md.library.append_note"
  ],
  "required_extensions": ["identity", "authz", "confirmation"]
}
```

---

## 6) Demo Checklist (What To Show Live)

For each prompt, show these six things explicitly:

1. user prompt
2. intent output (intent, confidence, risk, reason codes)
3. selected capabilities and execution order
4. policy/approval behavior (if mutate/destructive)
5. final response and artifact changes
6. audit/replay evidence

If these six are visible, the Router -> Intent Engine -> Agentic loop is understandable to both technical and non-technical audiences.

---

## 7) Why Use Protocol-Driven Agentic Workflows

Benefits of doing this through BDP instead of ad-hoc agent logic:

- composability: new capabilities are added as nodes, not hardcoded paths
- safety: extension gates and policy checks are consistent across steps
- observability: every step can be traced and replayed
- portability: memory/artifacts stay user-owned and swappable
- evolution: you can improve planner/orchestrator behavior without breaking core message contract
