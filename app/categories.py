from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.errors import OrphanCategoryHierarchy


def assemble_category_tree(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = {
        item["id"]: {**item, "children": []} for item in items
    }

    roots: list[dict[str, Any]] = []
    for node in nodes.values():
        parent_id = node.get("parent_id")
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
