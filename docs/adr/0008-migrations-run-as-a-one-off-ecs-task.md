# Migrations run as a one-off ECS task before each deploy

The database sits in a private subnet that only the app's security group can reach. GitHub's runners and laptops can't reach it, and that is deliberate. So the deploy pipeline starts a one-off ECS task from the new image, inside the private subnet, that runs `alembic upgrade head`. It waits for that task to exit successfully, and only then rolls the service onto the new image.

Running migrations on container start was rejected because two tasks starting together would race each other. Running them from a laptop through a tunnel was rejected because it would open a path to the database.
