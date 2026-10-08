# The app validates Cognito tokens itself, not the ALB

The ALB can run the Cognito login itself (`authenticate-cognito`) and pass a signed identity header to the app. We don't use that. It only covers browser sessions, so the JSON API couldn't use it. It can't run locally. And it would split identity between the load balancer and the app.

Instead, one `current_user` dependency validates the JWT against Cognito's public signing keys (JWKS) and maps Cognito groups to Requester, Agent and Admin. The same dependency accepts the dev-only login picker locally, and Bearer tokens on the API.
