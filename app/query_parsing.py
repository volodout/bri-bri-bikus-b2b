from __future__ import annotations

import re
from typing import Iterable

from app.errors import InvalidRequest

ALLOWED_SORTS: tuple[str, ...] = (
    "price_asc",
    "price_desc",
    "popularity",
    "new",
)

# Public B2C sort tokens -> B2B `/api/v1/public/products` enum
# (price_asc, price_desc, created_desc, popular). Forwarding the B2C token
# verbatim would make B2B 400 on `popularity`/`new`.
_SORT_B2C_TO_B2B: dict[str, str] = {
    "price_asc": "price_asc",
    "price_desc": "price_desc",
    "popularity": "popular",
    "new": "created_desc",
}

# Public B2C contract uses the singular `filter[key]` deepObject key. B2B
# expects the plural `filters[key]` — we translate on the way out
# (see `extract_filters`).
_FILTER_KEY_RE = re.compile(r"^filter\[([A-Za-z0-9_]+)\]$")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def validate_sort(value: str | None) -> str | None:
    """Validate the public B2C sort token and translate it to the B2B enum.

    Returns the B2B-side value (or None) ready to forward upstream. The error
    message still lists the B2C-contract tokens the client may send.
    """
    if value is None or value == "":
        return None
    if value not in ALLOWED_SORTS:
        raise InvalidRequest(
            "Invalid sort parameter. Allowed: " + ", ".join(ALLOWED_SORTS)
        )
    return _SORT_B2C_TO_B2B[value]


def validate_pagination(limit: int | None, offset: int | None) -> tuple[int, int]:
    effective_limit = 20 if limit is None else limit
    effective_offset = 0 if offset is None else offset
    if effective_limit < 1 or effective_limit > 100:
        raise InvalidRequest("limit must be between 1 and 100")
    if effective_offset < 0:
        raise InvalidRequest("offset must be >= 0")
    return effective_limit, effective_offset


SIMILAR_LIMIT_DEFAULT = 10
SIMILAR_LIMIT_MAX = 50


def validate_similar_limit(limit: int | None) -> int:
    effective = SIMILAR_LIMIT_DEFAULT if limit is None else limit
    if effective < 1 or effective > SIMILAR_LIMIT_MAX:
        raise InvalidRequest(f"limit must be between 1 and {SIMILAR_LIMIT_MAX}")
    return effective


def validate_uuid(value: str | None, *, field: str) -> str | None:
    if value is None or value == "":
        return None
    if not _UUID_RE.match(value):
        raise InvalidRequest(f"{field} must be a valid UUID")
    return value


SEARCH_MIN_LENGTH = 3
SEARCH_MAX_LENGTH = 200


def validate_search(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    if len(value) < SEARCH_MIN_LENGTH:
        raise InvalidRequest(f"Search query must be at least {SEARCH_MIN_LENGTH} characters")
    if len(value) > SEARCH_MAX_LENGTH:
        raise InvalidRequest(f"Search query must be at most {SEARCH_MAX_LENGTH} characters")
    return value


def extract_filters(items: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Pull public `filter[key]=value` pairs and re-emit them as B2B's
    plural `filters[key]=value`.

    Preserves order and allows repeated values (e.g. multi-select).
    """
    result: list[tuple[str, str]] = []
    for key, value in items:
        match = _FILTER_KEY_RE.match(key)
        if match:
            result.append((f"filters[{match.group(1)}]", value))
    return result
