"""REGENT_SERVICE_MODE: novel | legacy | combined."""

from __future__ import annotations

from enum import Enum
from typing import Any


class ServiceMode(str, Enum):
    NOVEL = "novel"
    LEGACY = "legacy"
    COMBINED = "combined"

    @property
    def includes_novel(self) -> bool:
        return self in (ServiceMode.NOVEL, ServiceMode.COMBINED)

    @property
    def includes_legacy(self) -> bool:
        return self in (ServiceMode.LEGACY, ServiceMode.COMBINED)


def parse_service_mode(raw: str | None) -> ServiceMode:
    text = (raw or ServiceMode.COMBINED.value).strip().lower()
    try:
        return ServiceMode(text)
    except ValueError as exc:
        raise ValueError(
            f"invalid REGENT_SERVICE_MODE={raw!r}; expected novel|legacy|combined"
        ) from exc


def resolve_service_mode(settings: Any | None = None) -> ServiceMode:
    if settings is None:
        from regent.config import get_settings

        settings = get_settings()
    raw = getattr(settings, "service_mode", None)
    if isinstance(raw, ServiceMode):
        return raw
    return parse_service_mode(str(raw) if raw is not None else None)
