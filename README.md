# Cloud-Helpdesk

[![CI](https://github.com/AcroIsTrash/Cloud-Helpdesk/actions/workflows/ci.yml/badge.svg)](https://github.com/AcroIsTrash/Cloud-Helpdesk/actions/workflows/ci.yml)

An AI-powered IT service desk, built to run on AWS.

The story this project tells: **I understand the infrastructure AI runs on,
from the network layer up.** It starts from a working service desk with real
ITSM rules, then moves it onto AWS (VPC, ECS Fargate, RDS, an ALB) and adds AI
features that are measured against a rule-based baseline before they're
trusted. Every trade-off is written down in an [ADR](docs/adr/), and the domain
terms are defined in the [glossary](GLOSSARY.md).

![Ticket list showing priorities, statuses, queues and SLA countdowns](docs/screenshots/ticket-list.png)

<table>
  <tr>
    <td><img src="docs/screenshots/ticket-detail-dark.png" alt="Ticket detail in dark mode with timeline, internal note and SLA clocks"></td>
    <td><img src="docs/screenshots/new-ticket.png" alt="New ticket form with the impact and urgency priority matrix"></td>
  </tr>
  <tr>
    <td align="center">Ticket detail: audit timeline, internal note, SLA clocks</td>
    <td align="center">New ticket: priority comes from the impact × urgency matrix</td>
  </tr>
</table>

## Where it is now

The app has been imported from [`helpdesk`](https://github.com/AcroIsTrash/helpdesk)
with its behaviour unchanged: FastAPI, server-rendered Jinja2 pages, SQLite.
Postgres, migrations, login and the AWS deployment come next; the
[stack](docs/stack.md) lists every piece and why it was chosen.

- [x] Import the app on Python 3.13, with CI
- [ ] Phase 1: Postgres + Alembic, a login seam, docker-compose
- [ ] Phase 2: Terraform (VPC, ECS Fargate, RDS, ALB) and a deploy pipeline
- [ ] Phase 3: an LLM router, evaluated against the keyword baseline
- [ ] Phase 4: suggested replies, summaries, duplicate detection
- [ ] Phase 5: observability and a load test

## What the service desk does

**Ticket lifecycle as a state machine.** `new → open → in_progress → pending → resolved → closed`,
with only legal moves allowed (`ALLOWED_TRANSITIONS` in `app/models.py`).
Resolving requires a resolution note; closing an untriaged ticket (duplicate,
spam) requires a reason. Requesters can only confirm or reject a resolution on
their own tickets.

**Priority is derived, not chosen.** The requester sets *impact* and *urgency*;
an ITIL-style matrix turns that into P1–P4, so "everything is urgent" can't
inflate priority.

**Two SLA clocks per ticket.** Time to first public response, and time to
resolution minus time spent waiting on the requester (`pending` pauses the
clock). Pausing never un-breaches a target that was already missed.

| Priority | First response | Resolution |
|---|---|---|
| P1 | 15 min | 4 h |
| P2 | 1 h | 8 h |
| P3 | 4 h | 24 h |
| P4 | 8 h | 72 h |

**Routing that can't lose a ticket.** New tickets are routed to a team queue by
a `Router`. Today that's `KeywordRouter`; an LLM router will plug into the same
interface. If a router raises or returns a decision that breaks the contract,
the ticket goes to the triage queue and a `routing_fallback` event records why.

**Audit trail.** Every change is written to an append-only `events` table in the
same transaction as the change, and shown on the ticket timeline.

The rules live in `app/services.py`. The JSON API and the HTML pages both call
it, so each rule is enforced in exactly one place.

## Run it

Requires [uv](https://docs.astral.sh/uv/). It installs Python 3.13 and the
exact dependency versions in `uv.lock`.

```bash
uv sync
uv run python -m uvicorn app.main:app --reload
```

Open http://localhost:8000; the API docs are at http://localhost:8000/docs. The
first launch creates demo users and tickets. The **Acting as** menu (top right)
switches between agent and requester views; real login comes in phase 1.

| Setting | Default | Effect |
|---|---|---|
| `HELPDESK_DB` | `helpdesk.db` | Path of the SQLite database file |
| `HELPDESK_DEMO` | `1` | Set to `0` to start without demo tickets |

## Run the tests and checks

```bash
uv run pytest            # business rules + API/UI smoke test
uv run ruff check .      # lint
uv run ruff format .     # format
uv run mypy              # types (strict)
```

The rule tests pass a fixed clock into every function, so SLA timing is tested
to the minute. CI (`.github/workflows/ci.yml`) runs the same three jobs,
`lint`, `typecheck` and `test`, on every push and pull request.

## Project layout

```
app/
  models.py     policy: statuses, transitions, priority matrix, SLA targets, schemas
  services.py   business rules; knows nothing about HTTP
  routing.py    Router interface + KeywordRouter baseline
  db.py         SQLite schema and seed users
  seed.py       demo tickets
  main.py       FastAPI: JSON API under /api, HTML pages elsewhere
  templates/    Jinja2 pages
tests/          pytest
docs/adr/       architecture decisions
docs/stack.md   the planned stack and why
GLOSSARY.md     domain terms
```

## Decisions made on the import

- **One Python version, 3.13**, in `.python-version`, `requires-python` and CI
  (which reads `.python-version` through uv). One version means one set of
  behaviours to test; the base image will use the same one.
- **`uv.lock` is the only dependency source.** The old `requirements*.txt`
  exports were dropped, so there's nothing to drift out of sync.
- **mypy in strict mode.** Typing the import surfaced places where the code
  relied on columns never being NULL (the SLA arithmetic); those are now
  explicit. The fixes were checked by diffing the API and HTML output before
  and after: identical.

## Known simplifications (for now)

- No authentication: the "Acting as" menu stands in for it until phase 1.
- SQLite with a connection per request; Postgres replaces it in phase 1.
- No migrations yet: delete `helpdesk.db` after a schema change.
- SLAs use calendar time, not business hours.
