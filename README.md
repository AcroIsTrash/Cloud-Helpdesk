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
with its behaviour unchanged, and now runs on PostgreSQL with Alembic
migrations. Every request now belongs to a logged-in person. Deactivation,
docker-compose and the AWS deployment come next; the
[stack](docs/stack.md) lists every piece and why it was chosen.

- [x] Import the app on Python 3.13, with CI
- [x] Postgres + Alembic, tests against a real database
- [x] A login seam: one `current_user` for pages and API, a dev login picker
- [ ] Phase 1 (rest): Deactivation, docker-compose
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

Requires [uv](https://docs.astral.sh/uv/) (it installs Python 3.13 and the
exact dependency versions in `uv.lock`) and a PostgreSQL 16 database. Docker
gives you one:

```bash
docker run -d --name helpdesk-db -p 5432:5432 \
  -e POSTGRES_USER=helpdesk -e POSTGRES_PASSWORD=helpdesk -e POSTGRES_DB=helpdesk \
  pgvector/pgvector:pg16
uv sync
uv run alembic upgrade head      # creates the schema; the app never does
HELPDESK_DEV_LOGIN=1 uv run python -m uvicorn app.main:app --reload
```

Open http://localhost:8000 and pick someone on the login page; the API docs are
at http://localhost:8000/docs. The first launch creates demo users and tickets.
Log out (top right) and back in as someone else to compare a Requester's view
with an Agent's.

| Setting | Default | Effect |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://helpdesk:helpdesk@localhost:5432/helpdesk` | The database the app and Alembic use |
| `HELPDESK_DEMO` | `1` | Set to `0` to start without demo tickets |
| `HELPDESK_DEV_LOGIN` | `0` | Set to `1` to enable the dev login picker (anyone can log in as anyone) |
| `HELPDESK_ENV` | `local` | `local` or `aws`; the app refuses to start with the picker on in `aws` |
| `HELPDESK_SESSION_SECRET` | random per start | Signs the session cookie; without it, a restart logs everyone out |

## Run the tests and checks

```bash
uv run pytest            # business rules + API/UI smoke test, on real Postgres
uv run ruff check .      # lint
uv run ruff format .     # format
uv run mypy              # types (strict)
uv run alembic check     # declared tables and migrations agree (needs a migrated DB)
```

The tests need Docker: [testcontainers](https://testcontainers.com/) starts one
Postgres for the session, runs the real Alembic migrations on it, and empties
the tables before each test. To use a database you already have running
instead, set `TEST_DATABASE_URL` (it gets truncated, so make it a throwaway).

The rule tests pass a fixed clock into every function, so SLA timing is tested
to the minute. CI (`.github/workflows/ci.yml`) runs four jobs on every push and
pull request: `lint`, `typecheck`, `test`, and `migrations`, which upgrades an
empty database to head and then fails if autogenerate still finds a difference
from the declared tables (a table changed without a migration).

The API contract is pinned too: `tests/openapi.json` is a snapshot of the
OpenAPI schema, and the suite fails if the schema drifts from it. This caught
a real slip during the import, where adding type hints quietly changed the
schema (FastAPI turns a return annotation into a response model). When an API
change is intended, regenerate the snapshot and commit it with the change, so
the PR diff shows exactly what changed:

```bash
UPDATE_SNAPSHOTS=1 uv run pytest tests/test_openapi.py
```

After changing a table in `app/db.py`, generate the migration and read it
before committing:

```bash
uv run alembic revision --autogenerate -m "what changed"
```

## Login: authentication, authorization, accounting

Every request belongs to a logged-in person, and who that person is comes from
one place: the `current_user` dependency in `app/main.py`. Every page and API
route takes it, except `/healthz` and the login page itself. No request body
says who is acting any more; before this, the HTML trusted an "Acting as"
cookie holding any user id and the API trusted an `actor_id` field, so a
Requester could assign tickets or read Internal notes by typing an Agent's id.

The design splits along the classic **AAA** lines, the same split a network
engineer knows from RADIUS and TACACS+:

- **Authentication: who are you?** An *identity source* (`app/auth.py`) takes a
  request and returns a person's id, or nothing. Phase 1 has one, the dev login
  picker: choose a person from a list and get a session cookie, signed with
  HMAC so the browser can't edit it into another id. The JSON API reads the
  same cookie for now.
- **Authorization: what may you do?** The permission rules in the service
  layer (`app/services.py`): a Requester sees and comments on only their own
  Tickets (anything else is a 403, even a guessed Ticket number), and only
  Agents assign, triage or write Internal notes. The web layer passes the
  logged-in person in and decides nothing itself. That closed two holes in the
  API, which used to call the services without a viewer: any caller could read
  any Ticket, Internal notes included, and list every Ticket
  (`tests/test_api.py::test_requester_cannot_open_someone_elses_ticket`).
- **Accounting: what did you do?** Every change writes an Event naming the
  person, in the same transaction as the change. Because that person now comes
  from the session, the audit trail can be trusted.

Not logged in, a page redirects to `/login` and the API answers 401.

**Why a seam, and why Cognito drops in later.** Routes ask `current_user`; only
the identity source knows how login works. Phase 2 adds a source that
validates Cognito JWTs (cookie for pages, Bearer token for the API) and maps
Cognito groups to Requester, Agent and Admin
([ADR-0012](docs/adr/0012-app-validates-cognito-tokens.md)). No route changes.

**A picker that can't leak into production.** The picker lets anyone be
anyone, which is exactly what local testing needs and exactly what must never
reach AWS. It's off unless `HELPDESK_DEV_LOGIN=1`, and the app refuses to
start if it's on with `HELPDESK_ENV=aws` (`tests/test_config.py`). A config
mistake fails the deploy instead of opening the app.

**Rejecting the old request shape.** Removing `actor_id` (and `requester_id`
on ticket creation) is a breaking API change. The tempting soft option is to
ignore the field, but then an old client keeps "working" while its requests
are silently attributed to whoever is logged in. Instead, every request body
now refuses unknown fields with a 422 (`extra="forbid"`; `tests/test_api.py`
checks each one), so a client we don't control fails loudly and its owner
finds out. The schema says so too: `additionalProperties: false` in the
OpenAPI snapshot.

## Project layout

```
app/
  models.py     policy: statuses, transitions, priority matrix, SLA targets, schemas
  services.py   business rules; knows nothing about HTTP
  routing.py    Router interface + KeywordRouter baseline
  db.py         table declarations (SQLAlchemy Core) and the engine
  seed.py       demo users and tickets
  auth.py       identity sources: who is making a request (the login seam)
  config.py     settings from environment variables, and the startup guard
  main.py       FastAPI: JSON API under /api, HTML pages elsewhere
  templates/    Jinja2 pages
migrations/     Alembic migrations, generated from app/db.py
tests/          pytest (conftest.py starts Postgres)
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
- **Enums stay `class Status(str, Enum)`.** Ruff suggests `StrEnum`, but that
  changes how members print in f-strings, which is a behaviour change, so the
  rule is switched off rather than "fixed".
- **mypy in strict mode.** Typing the import surfaced places where the code
  relied on columns never being NULL (the SLA arithmetic); those are now
  explicit. The fixes were checked by diffing the API and HTML output before
  and after: identical.

## Friction points: SQLite to Postgres

What broke, or would have broken quietly, when the app moved from `sqlite3`
to PostgreSQL through SQLAlchemy Core ([ADR-0003](docs/adr/0003-postgres-is-the-only-datastore.md),
[ADR-0007](docs/adr/0007-sqlalchemy-core-without-the-orm.md)), and
how each was fixed. The service and API tests were the safety net: they had to
pass against Postgres with their assertions unchanged.

- **Timestamps were text.** SQLite stored ISO strings and the code parsed them
  back with `datetime.fromisoformat` everywhere it did SLA arithmetic. The
  columns are now `timestamptz` and come back as datetimes, so the parsing
  helpers are gone. Two traps came with that:
  - *Time zone of the session.* Postgres returns `timestamptz` values in the
    connection's `TimeZone` setting, not in UTC. A session in Berlin gets
    09:00 UTC back as `10:00:00+01:00`, and the API output would change with it. The engine pins every
    connection to UTC (`options=-c timezone=UTC` in `app/db.py`).
  - *Microseconds.* The old code wrote `isoformat(timespec="seconds")`;
    Postgres keeps microseconds, so the API would suddenly return
    `09:00:00.483211+00:00`. `utcnow()` now drops them at the source.
- **`lastrowid` doesn't exist in Postgres.** New ticket and comment ids are read
  back with `INSERT ... RETURNING id`, in the same statement.
- **`internal` was `0`/`1`.** SQLite has no boolean, so the code did
  `int(...)` on the way in and `bool(...)` on the way out. It's a `boolean`
  column now, and the "hide internal notes" filter moved into the query.
- **`with conn:` doesn't translate.** In `sqlite3` that block commits or rolls
  back. SQLAlchemy starts a transaction on the *first* statement, which is
  usually a read (does this user exist?), so `conn.begin()` afterwards
  raises. Each command now runs inside a small `_atomic(conn)` block that
  commits the whole unit, its checks included, or rolls it all back. An Event
  still never commits without the change it records.
- **Two tasks, one ticket.** SQLite had one writer at a time; Postgres serves
  several app tasks at once, and under its default isolation two of them could
  read the same `pending` ticket and both bank its paused time. Commands now
  read the ticket `FOR UPDATE`, so the second waits for the first to commit
  and then sees the new state. A failed check rolls back at once, so it never
  sits on the lock (a test holds it to that).
- **Foreign keys were off.** SQLite ignores foreign keys unless every
  connection runs `PRAGMA foreign_keys = ON`. Postgres always enforces them, so
  the PRAGMA is gone.
- **The app created its own tables.** `executescript(CREATE TABLE IF NOT
  EXISTS ...)` on startup can't change an existing table, and two Fargate tasks
  starting together would race. Now one Alembic migration holds the schema and
  runs once per deploy as its own step
  ([ADR-0008](docs/adr/0008-migrations-run-as-a-one-off-ecs-task.md)). The app refuses to start on a
  database with no schema and tells you to run `alembic upgrade head`; a test
  checks it leaves the database empty.
- **Seeding raced the same way.** Demo data is "if the table is empty, insert",
  so 2–4 tasks booting together all saw an empty table: in a test with four
  simultaneous starts, three crashed on the duplicate email. Seeding now runs
  under a Postgres advisory lock, so one task seeds while the others wait,
  then find the data and skip.
- **Test isolation got harder.** A fresh in-memory SQLite per test was free.
  Starting a Postgres per test would be slow, so there is one container per
  session and the tables are truncated before each test (`RESTART IDENTITY`,
  so ids start at 1 again). The usual trick of wrapping each test in a
  transaction and rolling it back doesn't fit here: the services commit, and the
  API test's requests run on connections of their own.
- **Priority order.** `ORDER BY priority` on text works because `"P1" < "P2"`
  in any collation. That's an assumption, so a test now pins "P1 first".
- **Docker Hub rate limits.** In a sandbox without image pulls, testcontainers
  can't start. `TEST_DATABASE_URL` points the suite at an existing Postgres
  instead. For cloud agent sessions, `.claude/hooks/session-start.sh` starts
  the container's own Postgres and sets it, so every session can run the tests.
  Your machine and CI still use Docker.

## Known simplifications (for now)

- Login is a dev-only picker until Cognito arrives in phase 2; its sessions
  last until the browser closes and are not tied to the person's `active` flag
  yet (Deactivation is the next ticket).
- SLAs use calendar time, not business hours.
