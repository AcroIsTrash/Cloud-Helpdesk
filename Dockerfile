# The service desk image: built from this checkout, nothing cloned, no tests or
# dev tools inside. Both stages use the same pinned Python, so the virtualenv
# built in the first runs unchanged in the second.

# ---- build: resolve dependencies from uv.lock ----------------------------------
FROM python:3.13-slim@sha256:bf44cdfcb76cd3b41e879bc058fc37ec5872002ccfde7fcb765e218cde0cd79c AS build
COPY --from=astral/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c /uv /usr/local/bin/uv
# Use the image's Python, never a downloaded one; compile bytecode once, here.
ENV UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
# --frozen: install exactly the lockfile, fail rather than re-resolve.
# --no-dev: no pytest, ruff, mypy or testcontainers in the image.
RUN uv sync --frozen --no-dev

# ---- runtime: the app, its migrations, and the virtualenv ---------------------
FROM python:3.13-slim@sha256:bf44cdfcb76cd3b41e879bc058fc37ec5872002ccfde7fcb765e218cde0cd79c
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin helpdesk
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations/ migrations/
COPY app/ app/
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# Not root: a compromised process can't write the app or install anything.
USER helpdesk
EXPOSE 8000
# All configuration comes from environment variables (see README, "Run it").
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
