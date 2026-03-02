# Workflow Basic Examples (Router -> Intent Engine)

Purpose: show how user prompts map to intent decisions and routing behavior, from simple chat to multi-component workflows.

This document is written for demos and explanation, especially for showing:

- why the intent engine picked an intent
- how router dispatch follows from that
- what changes when RAG/CAG/Markdown Library/Web Search are introduced

---

## 1) Baseline Workflow (No Tooling)

Flow:

`User Chat Prompt -> intent.router.natural-language -> node.router -> node.chat.general -> Display Response`

Prompt example:

`"Explain SMART goals in 3 bullets."`

Intent engine output (example):

```json
{
  "canonical_intent": "chat.general",
  "confidence": 0.94,
  "risk_class": "read",
  "reason_codes": ["plain_explanation_request"],
  "required_extensions": ["identity"]
}
```

Why this workflow:

- The prompt asks for a general explanation.
- No file, memory, or external lookup signal is present.
- Router can directly route to a chat/general capability.

---

## 2) Component Offer Summary (RAG, CAG, Markdown Library, Web Search)

This section spells out exactly what each component offers in the system.

## RAG (Retrieval-Augmented Generation)

What it offers:

- Pulls relevant chunks from internal indexed knowledge (docs/files) before generation.
- Improves factual grounding against your own document corpus.
- Best when prompt asks: "from our docs/files/specs, tell me..."

What it does not replace:

- It does not track live conversation state by itself (that is CAG).
- It does not persist new notes/files by itself (that is Markdown Library).

## CAG (Context-Augmented Generation)

What it offers:

- Recovers short-horizon conversation/session context (recent turns, references like "that", pending state).
- Helps disambiguate follow-up requests in active chats.
- Best when prompt asks: "what did I say before..." or uses implicit references.

What it does not replace:

- It is not a full durable document retrieval system (that is RAG/Markdown search).
- It is not external real-time information lookup (that is Web Search).

## Markdown Library

What it offers:

- Durable, user-owned file operations for notes and artifacts (create/read/append/delete).
- A concrete persistence layer the user can inspect and export.
- Best when prompt asks to manage notes or save outcomes.

What it does not replace:

- It does not discover fresh web information by itself.
- It does not automatically choose context windows or retrieval strategy.

## Web Search

What it offers:

- Fresh external information beyond local memory/docs.
- Source discovery for "latest/current/today" requests.
- Best when prompt depends on current public facts.

What it does not replace:

- It is not your internal memory or document system.
- It should not be treated as persistent workspace state unless written to Markdown Library.

Quick discriminator for routing:

- Internal indexed docs -> RAG
- Live conversational recall/disambiguation -> CAG
- Persistent note/file operations -> Markdown Library
- Fresh external facts/news -> Web Search

---

## 3) Single-Component Workflow Examples

## 3.1 Add RAG

Flow:

`User Prompt -> Intent: knowledge.rag.query -> Router -> node.rag -> LLM synthesis -> Display`

Prompt example:

`"From my Product Requirement docs, what risks were listed for onboarding?"`

Intent engine output (example):

```json
{
  "canonical_intent": "knowledge.rag.query",
  "confidence": 0.91,
  "risk_class": "read",
  "reason_codes": ["source_scoped_question", "internal_docs_reference"]
}
```

Why this workflow:

- Prompt explicitly references internal documents.
- Retrieval from indexed corpus is required before model response.

Exact component offer in this flow:

- RAG supplies internal-document evidence to the model.
- Router chooses RAG because the prompt scope is internal corpus, not live chat state.
- Output is better-grounded synthesis from retrieved internal chunks.

---

## 3.2 Add CAG

Flow:

`User Prompt -> Intent: context.cag.recall -> Router -> node.cag -> LLM synthesis -> Display`

Prompt example:

`"What did I say was my top priority in yesterday's chat?"`

Intent engine output (example):

```json
{
  "canonical_intent": "context.cag.recall",
  "confidence": 0.89,
  "risk_class": "read",
  "reason_codes": ["conversation_memory_lookup", "recent_context_reference"]
}
```

Why this workflow:

- Prompt asks to recall prior conversation context.
- Conversation/session memory retrieval is primary requirement.

Exact component offer in this flow:

- CAG supplies recent conversational context and resolves references.
- Router chooses CAG because the user asks about prior chat, not document corpus.
- Output is continuity-aware response for active conversation state.

---

## 3.3 Add Markdown Library

Flow:

`User Prompt -> Intent: md.library.create_note -> Router -> node.markdown.library -> Display Result`

Prompt example:

`"Create note called release-readiness with a checklist for launch day."`

Intent engine output (example):

```json
{
  "canonical_intent": "md.library.create_note",
  "confidence": 0.95,
  "risk_class": "mutate",
  "confirmation_required": true,
  "reason_codes": ["create_note_action"]
}
```

Why this workflow:

- Prompt clearly requests a filesystem/library mutation.
- Router should enforce policy + confirmation before execution.

Exact component offer in this flow:

- Markdown Library performs durable note operations.
- Router treats this as mutation and applies policy/confirmation gates.
- Output is persisted artifact state (created/updated/deleted note), not just chat text.

---

## 3.4 Add Web Search

Flow:

`User Prompt -> Intent: web.search.query -> Router -> node.web.search -> LLM synthesis -> Display`

Prompt example:

`"Find the latest updates on vector database pricing and summarize with sources."`

Intent engine output (example):

```json
{
  "canonical_intent": "web.search.query",
  "confidence": 0.92,
  "risk_class": "read",
  "reason_codes": ["external_fresh_info_request"]
}
```

Why this workflow:

- Prompt asks for latest external information.
- Internal memory alone is insufficient; web retrieval is needed.

Exact component offer in this flow:

- Web Search supplies fresh external sources.
- Router chooses Web Search because recency/currentness is required.
- Output is synthesis grounded in externally retrieved information.

---

## 4) Multi-Component Workflow Examples

## 4.1 RAG + Markdown Library

Flow:

`Prompt -> intent.router -> Router -> node.rag (read internal docs) -> node.markdown.library (write summary note) -> Display`

Prompt example:

`"Read our onboarding docs and create a note called onboarding-gaps with missing steps."`

Intent plan (example):

```json
{
  "canonical_intent": "workflow.compose",
  "target_capabilities": [
    "knowledge.rag.query",
    "md.library.create_note"
  ],
  "confidence": 0.9,
  "risk_class": "mutate",
  "confirmation_required": true,
  "reason_codes": ["multi_step_internal_read_then_write"]
}
```

Why this workflow:

- First part requires internal document retrieval (RAG).
- Second part requires writing artifact to library (Markdown mutation).

---

## 4.2 CAG + RAG + Web Search

Flow:

`Prompt -> intent.router -> Router -> node.cag -> node.rag -> node.web.search -> node.chat/general synthesis -> Display`

Prompt example:

`"Use what I said last week, compare it with our strategy docs and latest market news, then give me a revised 5-step plan."`

Intent plan (example):

```json
{
  "canonical_intent": "workflow.compose",
  "target_capabilities": [
    "context.cag.recall",
    "knowledge.rag.query",
    "web.search.query",
    "workflow.plan.generate"
  ],
  "confidence": 0.87,
  "risk_class": "read",
  "reason_codes": ["memory_plus_docs_plus_external_merge"]
}
```

Why this workflow:

- "what I said last week" -> CAG
- "our strategy docs" -> RAG
- "latest market news" -> Web Search
- "revised 5-step plan" -> synthesis/planning step

---

## 4.3 RAG + CAG + Web Search + Markdown Library

Flow:

`Prompt -> intent.router -> Router -> (CAG + RAG + Web Search) -> node.markdown.library append/create -> Display`

Prompt example:

`"Given what I promised in prior chats, our internal roadmap docs, and current competitor updates, append next actions to note weekly-review."`

Intent plan (example):

```json
{
  "canonical_intent": "workflow.compose",
  "target_capabilities": [
    "context.cag.recall",
    "knowledge.rag.query",
    "web.search.query",
    "md.library.append_note"
  ],
  "confidence": 0.9,
  "risk_class": "mutate",
  "confirmation_required": true,
  "reason_codes": ["multi_source_analysis_then_persist"]
}
```

Why this workflow:

- Multi-source evidence gathering is required.
- Final user goal is persistence to markdown library (mutation).

---

## 5) Prompt Signals -> Workflow Decision Cheatsheet

Use these language signals to explain routing decisions in demos:

- `"from my docs"`, `"in our files"` -> RAG
- `"what did I say before"`, `"from previous chat"` -> CAG
- `"latest"`, `"today"`, `"current news"` -> Web Search
- `"create note"`, `"append to note"`, `"delete note"` -> Markdown Library
- Mixed signals in one prompt -> composed multi-step workflow

---

## 6) Router + Intent Demo Talking Track

For each demo prompt, narrate in this order:

1. User prompt received.
2. Intent engine extracts canonical intent(s), confidence, risk class, reason codes.
3. Router checks capability availability and extension/policy requirements.
4. Router dispatches node sequence.
5. Final response and (if mutate/destructive) confirmation/policy gating result.

This makes the Router -> Intent Engine behavior visible and explainable to non-technical viewers.
