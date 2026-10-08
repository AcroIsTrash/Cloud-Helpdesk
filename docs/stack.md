# Stack

Status: **proposed**, not yet grilled into ADRs. Two principles drive it:
**keep what already works** (from `helpdesk` and `aws-agent`) and **stay
AWS-native** (one identity system, IAM instead of API keys, one story).

## Application

| Tech | Role | Why |
|---|---|---|
| Python 3.12+ | Language | Existing code; strongest AI tooling. |
| FastAPI | Web + JSON API | Existing; async so slow LLM calls don't block other requests; typed models; free OpenAPI docs. |
| Jinja2 server-rendered HTML | UI | Existing and good. A SPA doubles the work without serving the AI/infra story. HTMX later if a page needs it. |
| Pydantic v2 + pydantic-settings | Validation, config | Existing; also validates LLM output before anything trusts it. |
| SQLAlchemy 2.0 (Core-style) | DB access | Replaces raw `sqlite3`; pooling for Postgres; Alembic builds on it. Keep queries explicit so services read plainly. |
| Alembic | Migrations | Replaces "delete `helpdesk.db` after a schema change." |
| uv | Dependencies | Existing; lockfile gives reproducible images. |
| pytest + testcontainers | Tests | Existing fixed-clock tests, now against real Postgres. |
| ruff + mypy | Lint, types | Fast CI gate. |

## Data

| Tech | Role | Why |
|---|---|---|
| PostgreSQL 16+ (RDS) | Primary store | SQLite is one file on one host; multiple containers need a shared database with concurrent writes. The audit trail writes in the same transaction as each change. |
| pgvector | Ticket embeddings | Similar-ticket search for suggested replies and duplicate detection, in the same DB: one backup, joins onto `events`. |

## AI

| Tech | Role | Why |
|---|---|---|
| Amazon Bedrock | Model access | IAM task role calls it: no API key to store or rotate; billing and CloudTrail in AWS; swap models without code changes. |
| Claude, small (Haiku-class) | Routing, classification, summaries | Short structured decisions; cheap and fast enough for every ticket. |
| Claude, larger (Sonnet-class) | Suggested replies | Needs reasoning and writing quality; runs only when an agent asks. |
| Bedrock embeddings (Titan or Cohere) | Vectors for pgvector | Same IAM access; cheap. |
| Anthropic SDK (Bedrock client) or boto3, called directly | LLM calls | One structured call per feature. Direct calls are easier to test, debug and explain than a framework like LangChain. |
| Eval harness (pytest + labeled tickets) | Router vs `KeywordRouter` | Turns "added AI" into a measured result: accuracy, fallback rate, cost per ticket, latency. Queue-move events are a live error signal. |

Confirm which Claude models Bedrock offers in `us-east-1` when building the AI
phase; store model IDs in Parameter Store, not code.

## Login

| Tech | Role | Why |
|---|---|---|
| Amazon Cognito | Login, agent vs requester roles | Replaces the "Acting as" menu; managed, no password storage; app validates its JWTs and maps groups onto the existing permission rules. |

## Compute and networking (us-east-1)

| Tech | Role | Why |
|---|---|---|
| Docker | Packaging | Same image locally and in AWS. |
| ECR, immutable SHA tags | Image storage | Carried over from `aws-agent`; every running image traces to a commit; rollback = redeploy an older SHA. |
| ECS on Fargate | Run containers | No servers to manage, autoscaling, health-check restarts. EKS costs ~$70/mo for its control plane alone, overkill for one service. |
| Application Load Balancer | HTTPS entry | Spreads load across tasks, drops unhealthy ones, terminates TLS. |
| ACM + Route 53 | Cert + DNS | Free auto-renewing certs. Optional if no domain is bought. |
| VPC: public + private subnets, 2 AZs | Network | Only the ALB is public; app and DB sit in private subnets with no inbound internet route; survives one AZ failing. |
| VPC endpoints (ECR, Bedrock, Secrets Manager, CloudWatch Logs, S3 gateway) | Private AWS access | Avoids a NAT gateway (~$32/mo each, the classic surprise bill) and is itself a design talking point. |
| Security groups chained by reference | Firewall | Internet → ALB:443 → app → DB:5432, nothing else. No SSH; ECS Exec for a shell. |

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
| Terraform, split into modules (network, data, app) | All infrastructure | Carried over; each layer readable on its own. |
| S3 backend with native lockfile | State | Fixes local-only `tfstate` from `aws-agent`; Terraform ≥1.10 locks in S3, no DynamoDB table needed. |
| GitHub Actions + OIDC | Pipeline | test + lint → eval → build/push to ECR → migrate → deploy. No stored AWS keys. |
| ECS rolling deploy + circuit breaker | Release | Automatic rollback if new tasks fail health checks. Replaces SSM Run Command. |

## Observability and cost

| Tech | Role | Why |
|---|---|---|
| CloudWatch Logs (JSON) | Logs | Native to ECS. |
| CloudWatch metrics + dashboard | Latency, errors, LLM cost per ticket, AI fallback rate, draft acceptance rate | The AI numbers are the headline of Layer 4. |
| OpenTelemetry → X-Ray | Traces | Shows where time goes: DB, Bedrock, app. |
| AWS Budgets | Cost alarm | First resource, every time. |

## Local development

| Tech | Role | Why |
|---|---|---|
| docker-compose (app + `pgvector/pgvector` Postgres) | Local stack | Production-shaped, zero AWS cost. |
| Fake LLM client | Tests and local runs | Free, fast, deterministic. Only the eval harness calls real models. |

## Deliberately left out

- **EKS/Kubernetes**: cost and complexity for a single service.
- **LangChain**: an abstraction layer the use cases don't need.
- **Separate vector DB** (Pinecone, OpenSearch): pgvector covers it.
- **NAT gateway**: VPC endpoints instead.
- **React/SPA**: doesn't serve the story.
- **Self-hosted models (Ollama)**: a GPU instance costs far more than Bedrock
  at this volume; that's a separate project.
- **Aurora Serverless**: plain RDS is cheaper at this size; `db.t4g.micro` is
  free-tier for the first 12 months.

## Running cost

About **$50–90/month if left up 24/7** (ALB ~$16, RDS free-tier or ~$15,
Fargate ~$20, interface endpoints ~$7 each per AZ, Bedrock pennies at demo
volume). With `terraform destroy` between sessions, a few dollars a month.

## Open decisions

1. **Domain**: buy one (~$12/yr) or use the ALB hostname?
2. **Login timing**: Cognito in phase 1, or keep "Acting as" until later?
3. **RDS Multi-AZ**: resilient at double the cost, or single-AZ?
