from __future__ import annotations

import base64
import binascii
import json

from fastapi import Request

from app.errors import InvalidRequest, Unauthorized
from app.query_parsing import validate_uuid


def user_id_from_jwt(request: Request) -> str:
    authorization = request.headers.get("Authorization")
    if not authorization:
        raise Unauthorized()

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Unauthorized()

    parts = token.split(".")
    if len(parts) != 3:
        raise Unauthorized()

    try:
        payload_bytes = _base64url_decode(parts[1])
        payload = json.loads(payload_bytes)
    except (binascii.Error, UnicodeDecodeError, ValueError, TypeError):
        raise Unauthorized("Invalid token")

    sub = payload.get("sub")
    if not isinstance(sub, str):
        raise Unauthorized("Invalid token")

    try:
        user_id = validate_uuid(sub, field="sub")
    except InvalidRequest:
        raise Unauthorized("Invalid token")

    if user_id is None:
        raise Unauthorized("Invalid token")
    return user_id


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
