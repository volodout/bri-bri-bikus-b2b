from __future__ import annotations

from typing import Any

import jwt
from fastapi import Request

from app.config import settings
from app.errors import Forbidden, InvalidRequest, Unauthorized
from app.query_parsing import validate_uuid


def user_id_from_jwt(request: Request) -> str:
    claims = _claims_from_request(request)
    return _buyer_user_id_from_claims(claims)


def optional_user_id_from_jwt(request: Request) -> str | None:
    authorization = request.headers.get("Authorization")
    if not authorization:
        return None
    claims = _claims_from_request(request)
    return _buyer_user_id_from_claims(claims)


def _buyer_user_id_from_claims(claims: dict[str, Any]) -> str:
    role = claims.get("role")
    if role != "buyer":
        raise Forbidden()

    sub = claims.get("sub")
    if not isinstance(sub, str):
        raise Unauthorized("Invalid token")

    try:
        user_id = validate_uuid(sub, field="sub")
        validate_uuid(_string_claim(claims, "jti"), field="jti")
    except InvalidRequest:
        raise Unauthorized("Invalid token")

    if user_id is None:
        raise Unauthorized("Invalid token")
    return user_id


def _claims_from_request(request: Request) -> dict[str, Any]:
    authorization = request.headers.get("Authorization")
    if not authorization:
        raise Unauthorized()

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Unauthorized()

    try:
        claims = jwt.decode(
            token,
            key=_jwt_key(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "role", "iat", "exp", "jti"]},
        )
    except jwt.ExpiredSignatureError:
        raise Unauthorized("Token expired")
    except jwt.InvalidTokenError:
        raise Unauthorized("Invalid token")

    if not isinstance(claims, dict):
        raise Unauthorized("Invalid token")
    return claims


def _jwt_key() -> str:
    if settings.jwt_algorithm == "HS256":
        return settings.jwt_secret
    if settings.jwt_algorithm == "RS256":
        if not settings.jwt_public_key:
            raise Unauthorized("Invalid token")
        return settings.jwt_public_key
    raise Unauthorized("Invalid token")


def _string_claim(claims: dict[str, Any], name: str) -> str:
    value = claims.get(name)
    if not isinstance(value, str):
        raise Unauthorized("Invalid token")
    return value
