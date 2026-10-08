# The routing eval uses a hand-labeled set and runs when routing changes

The eval set (`evals/routing/tickets.jsonl`) is about 200 tickets. A model helped draft them for variety, but every correct Queue was set by a human. A model labeling its own test would only measure self-agreement. The set deliberately includes ambiguous and off-topic tickets, which exercise the Routing fallback. It is never reused as demo seed data, so the demo can't look good by having seen its answers.

The eval runs in CI only when a PR changes the router, its prompt or the eval set, and on demand. It uses a CI role that can only call Bedrock, so the deploy role stays narrow (ADR-0009). Each run reports accuracy, fallback rate, cost per ticket and p95 latency against the `KeywordRouter` baseline.
