from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.errors import OrphanCategoryHierarchy


def _to_ref(item: Mapping[str, Any]) -> dict[str, Any]:
    raw_path = item.get("path") or ""
    path = [segment for segment in str(raw_path).split("/") if segment]
    return {
        "id": item["id"],
        "name": item["name"],
        "parent_id": item.get("parent_id"),
        "level": int(item["level"]),
        "path": path,
    }


def to_category_refs(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [_to_ref(item) for item in items]


def assemble_category_tree(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = {
        item["id"]: {**_to_ref(item), "children": []} for item in items
    }

    roots: list[dict[str, Any]] = []
    for node in nodes.values():
        parent_id = node["parent_id"]
        if parent_id is None:
            roots.append(node)
            continue
        parent = nodes.get(parent_id)
        if parent is None:
            raise OrphanCategoryHierarchy(
                f"category {node['id']} references missing parent {parent_id}"
            )
        parent["children"].append(node)

    return roots
