# Terraform is split into a permanent foundation and a disposable runtime

Following ADR-0001, the infrastructure is two root modules.

- `infra/foundation` is applied once and never destroyed. It holds the budget alarm, the S3 state bucket, the GitHub OIDC provider and CI role, ECR, and the Route 53 hosted zone. All of these are free or nearly free, and destroying them would break CI or lose history.
- `infra/runtime` holds everything that bills by the hour: the VPC, ALB, ECS and RDS. It is destroyed between sessions.

The foundation layer bootstraps with local state, then migrates its own state into the bucket it created. The budget alarm is defined in code, unlike the earlier `aws-agent` project, where it was created by hand in the console.
