"""Ticket routing: decide category + queue from the ticket text.

`Router` is the seam for the later AI pass: write an `LLMRouter` with the same
`route()` signature, swap it in `main.py`, and nothing else changes. The
`reason` and `confidence` fields are logged to the audit trail, so an AI
router's decisions stay explainable and reviewable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from .models import QUEUE_FOR_CATEGORY, TRIAGE_QUEUE, Category


@dataclass(frozen=True)
class RoutingDecision:
    category: Category
    queue: str
    reason: str
    confidence: float  # 0.0–1.0


class Router(Protocol):
    def route(self, title: str, description: str) -> RoutingDecision: ...


KEYWORDS: dict[Category, list[str]] = {
    Category.NETWORK: [
        "vpn",
        "wifi",
        "wi-fi",
        "network",
        "internet",
        "dns",
        "ethernet",
        "latency",
        "firewall",
        "disconnect",
        "drops",
    ],
    Category.ACCESS: [
        "password",
        "locked out",
        "login",
        "log in",
        "mfa",
        "2fa",
        "permission",
        "permissions",
        "account",
        "sso",
    ],
    Category.HARDWARE: [
        "laptop",
        "monitor",
        "printer",
        "keyboard",
        "mouse",
        "dock",
        "docking",
        "battery",
        "screen",
        "headset",
    ],
    Category.SOFTWARE: [
        "install",
        "outlook",
        "excel",
        "teams",
        "crash",
        "crashes",
        "crashing",
        "error",
        "update",
        "license",
        "application",
    ],
}


class KeywordRouter:
    """Transparent rule-based baseline. Also the benchmark an AI router must beat."""

    def route(self, title: str, description: str) -> RoutingDecision:
        text = f"{title} {description}".lower()
        hits = {
            cat: [kw for kw in kws if re.search(rf"\b{re.escape(kw)}\b", text)]
            for cat, kws in KEYWORDS.items()
        }
        best = max(hits, key=lambda c: len(hits[c]))
        if not hits[best]:
            return RoutingDecision(
                Category.OTHER, TRIAGE_QUEUE, "no keyword match; sent to triage", 0.0
            )
        total = sum(len(v) for v in hits.values())
        return RoutingDecision(
            category=best,
            queue=QUEUE_FOR_CATEGORY[best],
            reason=f"matched keywords: {', '.join(hits[best])}",
            confidence=round(len(hits[best]) / total, 2),
        )
