# Interview Skill

When this skill is triggered, conduct a thorough interview with the user about the feature they want to build. Use the AskUserQuestion tool to ask questions in batches of 2-4 related questions at a time.
Interview Structure

Round 1: Core Understanding Ask about:

    What exactly are we building? (Get a clear description)
    What problem does this solve? (The pain point)
    Who is this for? (BrainDrive persona: Owner/Builder/Entrepreneur)
    What's the context of use? (When/where will they use this?)

Round 2: Scope & Intent Ask about:

    Is this a prototype (testing feasibility) or production feature?
    Is this a plugin or does it require core modifications?
    What does success look like? (How will we know it works?)
    What's explicitly NOT in scope?

Round 3: User Experience Ask about:

    Walk me through the primary user flow step by step
    What should the user see first?
    What happens when they complete the main action?
    Are there any secondary flows or edge cases?

Round 4: Technical Context Ask about:

    Any specific technologies required or forbidden?
    Integration points with existing BrainDrive features?
    Performance or security requirements?
    Dependencies on external services?

Round 5: Details & Edge Cases Ask about:

    What happens with invalid input?
    What happens when something goes wrong?
    Any data that needs to persist?
    Anything else I should know?

Round 6: Security Ask about:

    Does this feature handle user input or execute user-provided code?
    Does it touch authentication, authorization, or credentials?
    Does it introduce new network surfaces (APIs, webhooks, external calls)?
    Does it store or transmit sensitive data (PII, tokens, keys)?
    What's the blast radius if this feature is compromised?

Round 7: Verification & Testing Ask about:

    How will we know this feature works correctly? What does "correct" mean for this?
    What must ALWAYS be true? (e.g., "encrypted data must always decrypt back to the original", "User A can never see User B's data")
    What's the worst thing that could happen if this feature has a bug?
    Are there existing tests we need to keep passing?
    How should we monitor this in production? (Metrics, alerts, logs?)

Question Guidelines

    Be specific, not vague - "What happens when the user clicks Submit?" not "How does the form work?"
    Avoid obvious questions - Don't ask things you could infer
    Go deep - Follow up on interesting answers
    Challenge assumptions - "Are you sure you need X?" or "What if we simplified to just Y?"
    Use multiSelect when asking about features or options that aren't mutually exclusive

Completion Criteria

Continue interviewing until:

    You can clearly describe what we're building
    You know who it's for and what problem it solves
    You understand the primary user flow in detail
    Scope is clearly defined (what's in vs out)
    Technical approach is clear (or marked as needing research)
    Security implications are understood (or marked N/A for low-risk features)
    Verification approach is defined (invariants identified, monitoring considered)
    The user confirms they have nothing more to add

After Interview

Once the interview is complete:

    Summarize what you learned
    Recommend running /landscape to research existing open-source solutions before writing the spec
    If the user skips landscape research, offer to generate a feature spec with /feature-spec
    Ask if there's anything to clarify before proceeding

Example Questions

Good questions:

    "When the user creates a new entry, do they start with a blank form or are there default values/prompts?"
    "You mentioned 'simple settings' - can you give me 2-3 examples of settings users would configure?"
    "What should happen if the API call fails? Show an error? Retry? Fall back to cached data?"

Bad questions (too vague):

    "What do you want the feature to do?"
    "How should errors work?"
    "What's the UI like?"

Notes

    For big features, expect 20-40+ questions across multiple rounds
    It's better to over-interview than under-interview
    The user can always say "I haven't decided yet" - capture that as an open question
    Reference BrainDrive personas (Adam Carter, Katie Carter, Privacy-Focused Pat) when relevant
