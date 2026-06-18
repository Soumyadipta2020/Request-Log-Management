from __future__ import annotations

from request_log.settings import Settings


def assign_owner(settings: Settings, business_unit: str, platform: str) -> str:
    """Return the configured owner for a business unit and platform pair."""
    key = f"{business_unit}|{platform}"
    return settings.assignment_rules.get(key, settings.default_assignee)
