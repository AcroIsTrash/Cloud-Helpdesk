"""Settings, all read from environment variables, so one image runs locally and in ECS."""

from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast

Environment = Literal["local", "aws"]
ENVIRONMENTS = {"local", "aws"}


class ConfigError(RuntimeError):
    """The settings are unsafe or inconsistent; the app refuses to start."""


@dataclass(frozen=True)
class Settings:
    environment: Environment
    dev_login: bool  # the dev login picker: anyone can log in as anyone
    session_secret: str  # signs the picker's session cookie

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Settings:
        environment = env.get("HELPDESK_ENV", "local")
        if environment not in ENVIRONMENTS:
            raise ConfigError(f"HELPDESK_ENV must be one of {sorted(ENVIRONMENTS)}")
        # Off unless explicitly enabled, and never in AWS: a config mistake
        # must not open the deployed app to anyone who can pick a name.
        dev_login = env.get("HELPDESK_DEV_LOGIN", "0") == "1"
        if dev_login and environment == "aws":
            raise ConfigError("the dev login picker cannot be enabled with HELPDESK_ENV=aws")
        return cls(
            environment=cast(Environment, environment),
            dev_login=dev_login,
            # Without a fixed secret, sessions last until the app restarts.
            session_secret=env.get("HELPDESK_SESSION_SECRET") or secrets.token_urlsafe(32),
        )
