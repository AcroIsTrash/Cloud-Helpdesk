# Private subnets reach AWS through one NAT gateway, not interface endpoints

The proposed stack used VPC interface endpoints to avoid a NAT gateway, on the belief that endpoints were the cheaper option. They aren't. The app needs about six of them (ECR API, ECR Docker, CloudWatch Logs, Secrets Manager, SSM, Bedrock runtime), and each costs about $7.30/month per AZ. Across two AZs that's about $88/month, against about $33/month for one NAT gateway. Every new AWS dependency would also mean another endpoint, and Cognito's signing keys may not be reachable through one at all.

So the private subnets send outbound traffic through a single NAT gateway in one AZ. A free S3 gateway endpoint keeps ECR image layers off the NAT's per-GB charge. Nothing on the internet can open a connection to the app or the database, because the NAT gateway only translates connections that start from inside the VPC. That is PAT, port address translation, as covered by the CCNA.

## Considered options

- **Interface endpoints**: rejected on cost and upkeep. They remain the upgrade path if outbound traffic must never leave AWS's network.
- **A self-managed NAT instance** (e.g. fck-nat): cheapest, but a server to patch.
- **Public IPs on tasks**: free, but it gives up "the app has no route from the internet".

## Consequences

The NAT gateway's AZ is a single point of failure for outbound traffic, though not for inbound. That is acceptable for an on-demand demo (ADR-0001). Production would use one NAT gateway per AZ.
