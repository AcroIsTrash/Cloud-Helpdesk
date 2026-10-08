# Cloud-Helpdesk

An AI-powered IT help desk running on AWS, built as a **portfolio project**. The
story it tells: *"I understand the infrastructure AI runs on, from the network
layer up."* The owner is studying for the CCNA; networking decisions are part of
the showcase, so explain them, don't just make them.

The **README is the portfolio.** Recruiters read it, not the code. Every
trade-off gets a short "why" in the README or an ADR, and every friction point
that bit us gets written up (what broke, how it was fixed).

## Where this came from

This repo merges two earlier repos (both owned by `AcroIsTrash`):

- **`helpdesk`**: the app. FastAPI + SQLite + Jinja2 IT service desk: ticket
  state machine, ITIL priority matrix (impact × urgency), two SLA clocks that
  pause in `pending`, team queues, internal notes, append-only audit trail
  (`events` table). Rules live in `services.py`; the web layer is thin.
  `routing.py` has a `Router` protocol with a `KeywordRouter` baseline, and
  `create_ticket()` falls back to the triage queue (logging a
  `routing_fallback` event) if a router raises or breaks the contract.
- **`aws-agent`**: the infra pattern. Terraform VPC + ECR, GitHub Actions with
  OIDC (no stored AWS keys), images tagged by immutable git SHA, AWS Budgets
  alarm set before anything is created.

`helpdesk` stays untouched as the clean baseline; its code gets imported into
`app/` here. In a cloud session, fetch either repo with the `add_repo` tool if
it isn't already cloned beside this one.

## Status

- [x] Matt Pocock's skills vendored into `.claude/skills/` (see `SOURCE.md` there)
- [x] `/setup-matt-pocock-skills` (GitHub Issues on this repo; default triage labels; single-context docs; see `docs/agents/`)
- [x] Stack grilled and recorded as ADRs (`docs/adr/` 0001–0013, `GLOSSARY.md`)
- [ ] Phase 1: import app, Postgres + Alembic + auth, docker-compose
- [ ] Phase 2: Terraform (VPC, ECS Fargate, RDS, ALB) + deploy pipeline
- [ ] Phase 3: LLM router + eval harness against `KeywordRouter`
- [ ] Phase 4: suggested replies (pgvector retrieval), summaries, duplicate detection
- [ ] Phase 5: observability, load test (k6/Locust), SQS worker if the load test justifies it

Each phase ships on its own, working and documented, before the next starts.
Update this list when a phase lands.

## Ground rules

- **Rules live in one place.** Business rules stay in the service layer; the API
  and HTML routes both call it. New behaviour gets a test there first.
- **Time is injected.** SLA and rule tests pass a fixed clock; keep that pattern.
- **Routing can never lose a ticket.** Any AI router keeps the fallback contract:
  on error, timeout, or an invalid decision, the ticket goes to triage and the
  reason lands in the audit trail.
- **AI is advisory.** It routes, drafts, summarises and suggests; status
  changes and replies to requesters stay with humans and the state machine.
  Every AI decision records its reason and confidence in `events`.
- **Measure before claiming.** An AI feature ships with an eval against its
  baseline (accuracy, fallback rate, cost per ticket, latency).
- **Cost hygiene.** Budget alarm exists before any resource. Prefer
  `terraform destroy` between work sessions. Call out anything that bills
  hourly (NAT gateway, ALB, RDS, Fargate, interface endpoints) before adding it.
- **Least privilege.** OIDC for CI, IAM task roles for the app, secrets in
  Secrets Manager. Nothing secret in the repo, the image, or Terraform state
  outputs.
- **Reproducible images.** The app is built from this repo's own source at the
  tagged commit; dependencies come from `uv.lock`.

## Agent skills

### Issue tracker

GitHub Issues on `AcroIsTrash/Cloud-Helpdesk`. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical labels, unrenamed (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## The stack

The full stack, with the reason for each choice and what we deliberately left
out, is below. Change it only through an ADR, then update this file.

@docs/stack.md
