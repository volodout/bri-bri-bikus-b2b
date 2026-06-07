from __future__ import annotations

from typing import Any, Mapping

_PRODUCT_PUBLIC_FIELDS: tuple[str, ...] = (
    "id",
    "slug",
    "title",
    "description",
    "images",
    "status",
    "characteristics",
)

_SKU_PUBLIC_FIELDS: tuple[str, ...] = (
    "id",
    "name",
    "price",
    "discount",
    "image",
    "active_quantity",
    "characteristics",
)


def to_public_product(b2b: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {k: b2b[k] for k in _PRODUCT_PUBLIC_FIELDS if k in b2b}
    out["skus"] = [to_public_sku(s) for s in b2b.get("skus") or ()]
    return out


def to_public_sku(b2b: Mapping[str, Any]) -> dict[str, Any]:
    return {k: b2b[k] for k in _SKU_PUBLIC_FIELDS if k in b2b}


def _card_images(cover_image: Any) -> list[dict[str, Any]]:
    if not cover_image:
        return []
    return [{"url": cover_image, "ordering": 0}]


def to_public_card(b2b: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": b2b["id"],
        "name": b2b["title"],
        "min_price": int(b2b["min_price"]),
        "has_stock": True,
        "images": _card_images(b2b.get("cover_image")),
    }
    if "slug" in b2b:
        out["slug"] = b2b["slug"]
    return out
