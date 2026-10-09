"""Who is making a request: the identity seam (ADR-0012).

An identity source answers one question, "which person sent this request?",
with a user id or None. The web layer's `current_user` dependency asks it and
loads that person; nothing else in the app knows how login works. Phase 1 has
one source, the dev login picker. Phase 2 adds one that validates Cognito
tokens, and no route changes.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Protocol

from fastapi import Request, Response


class IdentitySource(Protocol):
    def identify(self, request: Request) -> int | None: ...


class NoLogin:
    """No login method is configured: every request is anonymous."""

    def identify(self, request: Request) -> int | None:
        return None


class DevLoginPicker:
    """Local development only: pick a person from a list, get a signed session cookie.

    The cookie holds the user id and an HMAC of it, so the browser can't edit
    it into someone else's id. Choosing who to be is the whole point of the
    picker, which is why the app refuses to enable it in AWS (see config.py).
    """

    COOKIE = "session"

    def __init__(self, secret: str) -> None:
        self._key = secret.encode()

    def _sign(self, user_id: int) -> str:
        return hmac.new(self._key, str(user_id).encode(), hashlib.sha256).hexdigest()

    def identify(self, request: Request) -> int | None:
        user_id, _, signature = request.cookies.get(self.COOKIE, "").partition(".")
        if not (user_id.isascii() and user_id.isdigit()) or not hmac.compare_digest(
            signature, self._sign(int(user_id))
        ):
            return None
        return int(user_id)

    def log_in(self, response: Response, user_id: int) -> None:
        response.set_cookie(
            self.COOKIE, f"{user_id}.{self._sign(user_id)}", httponly=True, samesite="lax"
        )

    def log_out(self, response: Response) -> None:
        response.delete_cookie(self.COOKIE, httponly=True, samesite="lax")
