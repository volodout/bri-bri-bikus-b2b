from __future__ import annotations

from fastapi import APIRouter, Request

from app.collections import CollectionService, validate_collections_pagination
from app.query_parsing import validate_uuid

router = APIRouter()


def get_collection_service(request: Request) -> CollectionService:
    return request.app.state.collection_service


@router.get("/api/v1/catalog/collections")
async def list_collections(request: Request) -> list:
    limit, offset = validate_collections_pagination(
        request.query_params.get("limit"),
        request.query_params.get("offset"),
    )
    service = get_collection_service(request)
    return await service.list_collections(limit, offset)


@router.get("/api/v1/catalog/collections/{collection_id}")
async def get_collection(request: Request, collection_id: str) -> dict:
    collection_id = validate_uuid(collection_id, field="collection_id")
    service = get_collection_service(request)
    return await service.get_collection(collection_id)
