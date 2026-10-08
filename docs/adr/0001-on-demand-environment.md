# The AWS environment runs on demand, not 24/7

The deployed stack is created with `terraform apply` when someone is working on it or demoing it, and destroyed afterwards. The README shows the system through a recorded walkthrough, screenshots and the architecture diagram, not a live link. Always-on would cost $50–150/month for a portfolio that recruiters mostly read rather than log into. Being able to rebuild the whole environment from scratch in minutes is itself part of the showcase.

## Consequences

- Hourly-billed resources (ALB, RDS, Fargate, NAT, interface endpoints) cost cents per session, so a design choice should never be made just to avoid an hourly line item.
- Database contents don't survive a destroy. A seed script loads demo tickets on every bring-up.
