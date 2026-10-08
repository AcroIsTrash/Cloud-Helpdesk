# Security groups are the only firewall; NACLs stay at the default

Traffic is filtered by security groups that reference each other: internet → ALB:443 → app:8000 → DB:5432, and nothing else. The network ACLs are left at AWS's default allow-all. Security groups are stateful, so return traffic is allowed automatically. NACLs are stateless: every rule needs a mirror rule for the ephemeral return ports (1024–65535), and getting one wrong is a classic outage. Here, custom NACLs would add no protection beyond what the security groups and the data tier's routing (ADR-0010) already give.
