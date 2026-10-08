# Models are called through Amazon Bedrock, not the Anthropic API

The app calls Claude through Bedrock using the ECS task's IAM role. There is no API key to store, rotate or leak. Billing and CloudTrail stay in the same AWS account. The model ID is a setting in Parameter Store, so a model can be swapped without a new image.

We accepted two costs. Each AWS account must request model access once. Newer models in `us-east-1` may only be reachable through a cross-region inference profile (`us.anthropic.*`), which can serve a request from another US region. The direct Anthropic API would get new models sooner, but it needs a stored secret and an internet path out of the VPC.
