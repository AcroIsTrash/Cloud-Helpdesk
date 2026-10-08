# Terraform runs from the owner's machine; CI only builds and deploys

`terraform apply` and `terraform destroy` run locally, with short-lived credentials from IAM Identity Center, through `make up` and `make down`.

The GitHub OIDC role stays narrow. It can push to one ECR repository, run the migration task, and update one ECS service. Letting CI run Terraform would need a near-admin role, and a push to `main` could then create resources that bill by the hour.

Because the environment is on demand (ADR-0001), the deploy job first checks whether the runtime exists. If it doesn't, the job stops after pushing the image, and the next `make up` deploys the latest image.
