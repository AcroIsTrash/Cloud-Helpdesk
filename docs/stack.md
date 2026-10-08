# Stack

Status: **decided**. Grilled into ADRs 0001–0013 (`docs/adr/`); change it only through a new ADR. Two principles drive it:
**keep what already works** (from `helpdesk` and `aws-agent`) and **stay
AWS-native** (one identity system, IAM instead of API keys, one story).

## Application

| Tech | Role | Why |
|---|---|---|
| Python 3.13 (one version everywhere) | Language | Existing code; strongest AI tooling. One pinned version across `.python-version`, `requires-python`, the base image and CI. |
| FastAPI | Web + JSON API | Existing; async so slow LLM calls don't block other requests; typed models; free OpenAPI docs. |
| Jinja2 server-rendered HTML | UI | Existing and good. A SPA doubles the work without serving the AI/infra story. HTMX later if a page needs it. |
| Pydantic v2 + pydantic-settings | Validation, config | Existing; also validates LLM output before anything trusts it. |
| SQLAlchemy 2.0 Core, declared tables, no ORM (ADR-0007) | DB access | Replaces raw `sqlite3`; pooling for Postgres; Alembic generates migrations from the declared tables. Queries stay explicit so services read plainly. |
| Alembic | Migrations | Replaces "delete `helpdesk.db` after a schema change." Runs as a one-off ECS task before each deploy (ADR-0008). |
| uv | Dependencies | Existing; lockfile gives reproducible images. |
| pytest + testcontainers | Tests | Existing fixed-clock tests, now against real Postgres. |
| ruff + mypy | Lint, types | Fast CI gate. |

## Data

| Tech | Role | Why |
|---|---|---|
| PostgreSQL 16+ (RDS, single-AZ `db.t4g.micro`) | Primary store (ADR-0003) | SQLite is one file on one host; multiple containers need a shared database with concurrent writes. The audit trail writes in the same transaction as each change. |
| pgvector | Ticket embeddings | Similar-ticket search for suggested replies and flagging likely Duplicates for an Agent to confirm (never closed automatically), in the same DB: one backup, joins onto `events`. |

## AI

| Tech | Role | Why |
|---|---|---|
| Amazon Bedrock | Model access (ADR-0004) | IAM task role calls it: no API key to store or rotate; billing and CloudTrail in AWS; swap models without code changes. |
| Claude, small (Haiku-class) | Routing, classification, summaries | Short structured decisions; cheap and fast enough for every ticket. |
| Claude, larger (Sonnet-class) | Suggested replies | Needs reasoning and writing quality; runs only when an agent asks. |
| Bedrock embeddings (Titan or Cohere) | Vectors for pgvector | Same IAM access; cheap. |
| Anthropic SDK (Bedrock client) or boto3, called directly | LLM calls | One structured call per feature. Direct calls are easier to test, debug and explain than a framework like LangChain. |
| Eval harness (pytest + ~200 hand-labeled tickets, ADR-0013) | Router vs `KeywordRouter` | Turns "added AI" into a measured result: accuracy, fallback rate, cost per ticket, latency. Runs in CI when routing changes. Also tunes the confidence threshold below which a decision becomes a Routing fallback (a Parameter Store setting). Queue-move events are a live error signal. |

Confirm which Claude models Bedrock offers in `us-east-1` when building the AI
phase; store model IDs in Parameter Store, not code.

## Login

| Tech | Role | Why |
|---|---|---|
| Amazon Cognito (phase 2) | Login; Requester, Agent and Admin groups | Replaces the "Acting as" menu; managed, no password storage; app validates its JWTs itself, not the ALB (ADR-0012), and maps groups onto the existing permission rules. Phase 1 builds the seam first: one `current_user` dependency for HTML and API, no `actor_id` in request bodies, and a dev-only login picker. |

## Compute and networking (us-east-1)

| Tech | Role | Why |
|---|---|---|
| Docker | Packaging | Same image locally and in AWS. Multi-stage build on `python:3.13-slim` pinned by digest; `uv sync --frozen`; app copied from this repo's checkout, never cloned at build time; runs as non-root; `/healthz` checks the DB for the ALB. |
| ECR, immutable full-SHA tags | Image storage | Carried over from `aws-agent` (which used short SHAs; full SHAs can't collide); every running image traces to a commit; rollback = redeploy an older SHA. |
| ECS on Fargate (ADR-0005), 2–4 tasks spread over both AZs | Run containers | No servers to manage, CPU autoscaling, health-check restarts; two tasks make the one-AZ-failure claim demonstrable. EKS costs ~$70/mo for its control plane alone, overkill for one service. |
| Application Load Balancer | HTTPS entry | Spreads load across tasks, drops unhealthy ones, terminates TLS. |
| ACM + Route 53 | Cert + DNS | Free auto-renewing certs. A bought domain is required: ACM can't certify the ALB's own hostname, and Cognito needs HTTPS callbacks. The hosted zone lives in the foundation layer (ADR-0002). |
| VPC `10.20.0.0/16`: public, app and data tiers × 2 AZs (ADR-0010) | Network | Only the ALB is public; app and DB have no inbound internet route; the data tier has no outbound route either; survives one AZ failing. |
| One NAT gateway + S3 gateway endpoint (ADR-0006) | Outbound from private subnets | Cheaper than the ~6 interface endpoints × 2 AZs it replaces, and covers every AWS dependency. Its PAT is the CCNA talking point. The free S3 gateway keeps ECR image layers off NAT data charges. |
| Security groups chained by reference; default NACLs (ADR-0011) | Firewall | Internet → ALB:443 → app:8000 → DB:5432, nothing else. No SSH; ECS Exec for a shell. |

## Async (only when measured need appears)

| Tech | Role | Why |
|---|---|---|
| SQS + Fargate worker | Background AI jobs | Start with routing done in the request with a tight timeout (the fallback makes that safe). Add the queue when the load test shows LLM latency hurting. |

## Secrets and config

| Tech | Role | Why |
|---|---|---|
| Secrets Manager | DB credentials | RDS-managed, rotated, injected by ECS at startup. |
| SSM Parameter Store | Settings, model IDs, feature flags | Free; change behaviour without a new image. |

## IaC and CI/CD

| Tech | Role | Why |
|---|---|---|
| Terraform: `infra/foundation` (permanent) + `infra/runtime` (network, data, app modules; destroyed between sessions) (ADR-0002) | All infrastructure | Each layer readable on its own; only the runtime bills hourly. Applied from the owner's machine via `make up` / `make down` (ADR-0009). |
| S3 backend with native lockfile | State | Fixes local-only `tfstate` from `aws-agent`; Terraform ≥1.10 locks in S3, no DynamoDB table needed. |
| GitHub Actions + OIDC; protected `main` | Pipeline | test + lint → eval → build/push to ECR → migrate → deploy. No stored AWS keys. The CI role can only push one ECR repo, run the migration task and update one service; deploy steps skip when the runtime is down (ADR-0009). Work lands through PRs with green CI; the OIDC trust accepts only `refs/heads/main`, so only a merge deploys. |
| ECS rolling deploy + circuit breaker | Release | Automatic rollback if new tasks fail health checks. Replaces SSM Run Command. |

## Observability and cost

| Tech | Role | Why |
|---|---|---|
| CloudWatch Logs (JSON) | Logs | Native to ECS. |
| CloudWatch metrics + dashboard | Latency, errors, LLM cost per ticket, AI fallback rate, Suggested reply outcomes (sent as is / edited with similarity / discarded), Duplicate flags confirmed | The AI numbers are the headline of Layer 4. |
| OpenTelemetry → X-Ray | Traces | Shows where time goes: DB, Bedrock, app. |
| AWS Budgets | Cost alarm | First resource, every time. |

## Local development

| Tech | Role | Why |
|---|---|---|
| docker-compose (app + `pgvector/pgvector` Postgres) | Local stack | Production-shaped, zero AWS cost. |
| Fake LLM client | Tests and local runs | Free, fast, deterministic. Only the eval harness calls real models. |

## Repository layout

```
app/              FastAPI app (imported from helpdesk)
migrations/       Alembic
tests/
evals/routing/    labeled set + harness
infra/foundation/ infra/runtime/{network,data,app}/
docs/adr/  GLOSSARY.md  README.md  Makefile  docker-compose.yml  Dockerfile
```

## Deliberately left out

- **EKS/Kubernetes**: cost and complexity for a single service.
- **LangChain**: an abstraction layer the use cases don't need.
- **Separate vector DB** (Pinecone, OpenSearch): pgvector covers it.
- **Interface VPC endpoints**: one NAT gateway is cheaper and simpler here (ADR-0006).
- **RDS Multi-AZ**: data is reseeded on every bring-up (ADR-0001); named in the README as the production upgrade.
- **Terraform in CI**: would need a near-admin role (ADR-0009).
- **React/SPA**: doesn't serve the story.
- **Self-hosted models (Ollama)**: a GPU instance costs far more than Bedrock
  at this volume; that's a separate project.
- **Aurora Serverless**: plain RDS is cheaper at this size; `db.t4g.micro` is
  free-tier for the first 12 months.

## Running cost

The environment runs on demand (ADR-0001). Up 24/7 it would be about
**$70–90/month** (NAT ~$33, ALB ~$16, RDS free-tier or ~$15, Fargate ~$20,
Bedrock pennies at demo volume). Brought up only for work and demos, it's a
few dollars a month; the foundation layer costs about $1/month (hosted zone,
ECR storage) plus the domain at ~$14/year.
