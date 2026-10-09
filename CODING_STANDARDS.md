# Coding standards

Read at review time. These are judgement calls; anything mechanical lives in
ruff, mypy, the tests or CI instead. The ground rules in `CLAUDE.md` and the
decisions in `docs/adr/` also apply.

## Concurrency

The app runs as 2–4 tasks against one Postgres (ADR-0003, ADR-0005), so
assume every code path runs in several tasks at the same moment.

- **Lock what you check.** A command that reads a row, checks it, then writes
  it reads that row `FOR UPDATE` inside the same transaction. A plain read
  lets two tasks act on the same old state (paused time banked twice).
- **Startup is concurrent too.** Anything that writes when the app boots is
  safe with every task booting at once: guard it with an advisory lock or an
  `ON CONFLICT` insert, or move it to a one-off task like the migrations
  (ADR-0008). "If the table is empty, insert" is the race to look for.
- **Transactions stay short.** Network calls (the router, Bedrock, anything
  over HTTP) finish before the transaction opens. A slow model call inside a
  transaction holds its row locks for the whole call.

## Claims

- **Back every claim with a check.** "Behaviour unchanged", "output identical"
  or "tests unchanged" in a commit message, README or PR is supported by a
  test, a snapshot or a diff the reviewer can rerun. Name it next to the claim.
