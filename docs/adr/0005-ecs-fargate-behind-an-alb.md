# The app runs on ECS Fargate behind an Application Load Balancer

We considered and rejected three alternatives:

- **EC2 with docker-compose** (the earlier `aws-agent` pattern): cheaper, but a server to patch, SSH to secure, and no health-checked rolling deploys.
- **App Runner**: simpler, but it hides the VPC, subnets, security groups and load balancer. Those layers are the point of a project that shows the infrastructure AI runs on.
- **Lambda with API Gateway**: a poor fit for a FastAPI and Jinja app that holds pooled database connections and waits seconds on LLM calls.

EKS was ruled out because the control plane alone costs about $70/month, for a single service.
