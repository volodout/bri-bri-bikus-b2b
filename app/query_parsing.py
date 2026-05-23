from __future__ import annotations

import re
from typing import Iterable

from app.errors import InvalidRequest

ALLOWED_SORTS: tuple[str, ...] = (
    "rating",
    "popularity",
    "price_asc",
    "price_desc",
    "date_desc",
    "discount_desc",
)

_FILTER_KEY_RE = re.compile(r"^filters\[([A-Za-z0-9_]+)\]$")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def validate_sort(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    if value not in ALLOWED_SORTS:
        raise InvalidRequest(
            "Invalid sort parameter. Allowed: " + ", ".join(ALLOWED_SORTS)
        )
    return value


def validate_pagination(limit: int | None, offset: int | None) -> tuple[int, int]:
    effective_limit = 20 if limit is None else limit
    effective_offset = 0 if offset is None else offset
    if effective_limit < 1 or effective_limit > 100:
        raise InvalidRequest("limit must be between 1 and 100")
    if effective_offset < 0:
        raise InvalidRequest("offset must be >= 0")
    return effective_limit, effective_offset


def validate_similar_pagination(limit: int | None, offset: int | None) -> tuple[int, int]:
    effective_limit = 8 if limit is None else limit
    effective_offset = 0 if offset is None else offset
    if effective_limit < 1 or effective_limit > 20:
        raise InvalidRequest("limit must be between 1 and 20")
    if effective_offset < 0:
        raise InvalidRequest("offset must be >= 0")
    return effective_limit, effective_offset


def validate_uuid(value: str | None, *, field: str) -> str | None:
    if value is None or value == "":
        return None
    if not _UUID_RE.match(value):
        raise InvalidRequest(f"{field} must be a valid UUID")
    return value


def validate_search(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    if len(value) < 3:
        raise InvalidRequest("Search query must be at least 3 characters")
    if len(value) > 255:
        raise InvalidRequest("Search query must be at most 255 characters")
    return value


def extract_filters(items: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Pull `filters[key]=value` pairs from raw query items.

    Preserves order and allows repeated values (e.g. multi-select).
    """
    result: list[tuple[str, str]] = []
    for key, value in items:
        match = _FILTER_KEY_RE.match(key)
        if match:
            result.append((f"filters[{match.group(1)}]", value))
    return result
