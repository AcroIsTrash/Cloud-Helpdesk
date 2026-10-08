# Three subnet tiers; the data tier has no route out

The VPC `10.20.0.0/16` has three tiers of /24 subnets across two AZs:

| Tier | AZ a | AZ b | Default route |
|---|---|---|---|
| public (ALB, NAT) | 10.20.0.0/24 | 10.20.1.0/24 | internet gateway |
| app (Fargate) | 10.20.10.0/24 | 10.20.11.0/24 | NAT gateway |
| data (RDS) | 10.20.20.0/24 | 10.20.21.0/24 | none (local only) |

The obvious design is two tiers, with the database sharing the private subnets. A separate data tier with no default route means that even a compromised database can't reach the internet. This is enforced by routing, independently of security groups. RDS needs a subnet group in two AZs even when it runs single-AZ, which is why the data tier exists in both.

`10.20/16` avoids `10.0.0.0/16`, the range many home routers and VPNs default to, so a future VPN or peering link won't overlap. AWS reserves 5 addresses in every subnet, so each /24 holds 251 usable addresses.
