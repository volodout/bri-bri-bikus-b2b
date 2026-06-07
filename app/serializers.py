from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

_IMAGE_FIELDS: tuple[str, ...] = ("id", "url", "ordering", "alt", "is_main")


def _to_image(b2b: Mapping[str, Any]) -> dict[str, Any]:
    return {k: b2b[k] for k in _IMAGE_FIELDS if k in b2b}


def _to_attributes(characteristics: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    return {c["name"]: c["value"] for c in characteristics if "name" in c and "value" in c}


def _effective_price(sku: Mapping[str, Any]) -> int:
    return int(sku["price"]) - int(sku.get("discount", 0))


def to_public_sku(b2b: Mapping[str, Any]) -> dict[str, Any]:
    discount = int(b2b.get("discount", 0))
    out: dict[str, Any] = {
        "id": b2b["id"],
        "price": _effective_price(b2b),
        "old_price": int(b2b["price"]) if discount > 0 else None,
        "available_quantity": int(b2b.get("active_quantity", 0)),
    }
    if "name" in b2b:
        out["name"] = b2b["name"]
    if b2b.get("article") is not None:
        out["sku_code"] = b2b["article"]
    images = [_to_image(i) for i in b2b.get("images") or ()]
    if images:
        out["images"] = images
    attributes = _to_attributes(b2b.get("characteristics") or ())
    if attributes:
        out["attributes"] = attributes
    return out


def to_public_product(b2b: Mapping[str, Any]) -> dict[str, Any]:
    raw_skus: Sequence[Mapping[str, Any]] = b2b.get("skus") or ()
    in_stock = [s for s in raw_skus if int(s.get("active_quantity", 0)) > 0]
    price_pool = in_stock or raw_skus
    out: dict[str, Any] = {
        "id": b2b["id"],
        "name": b2b["title"],
        "min_price": min((_effective_price(s) for s in price_pool), default=0),
        "has_stock": bool(in_stock),
        "images": [_to_image(i) for i in b2b.get("images") or ()],
        "description": b2b["description"],
        "skus": [to_public_sku(s) for s in raw_skus],
    }
    if "slug" in b2b:
        out["slug"] = b2b["slug"]
    attributes = _to_attributes(b2b.get("characteristics") or ())
    if attributes:
        out["attributes"] = attributes
    return out
