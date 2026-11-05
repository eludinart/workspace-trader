"""Bearer auth for the Cursor control API. Fail closed when the token is missing."""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Annotated, Any

from fastapi import Header, HTTPException

_SECRET_MARKERS = ("token", "password", "secret", "api_key")


def control_token() -> str:
    return os.environ.get("TRADER_CONTROL_TOKEN", "").strip()


def _production() -> bool:
    return os.environ.get("APP_ENV", "").strip().lower() == "production"


def _tokens_match(presented: str, expected: str) -> bool:
    left = hashlib.sha256(presented.encode("utf-8")).digest()
    right = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(left, right)


def presented_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, rest = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = rest.strip()
    return token or None


def authorize_control(authorization: str | None, *, mutate: bool) -> None:
    """401 when the bearer token is missing or wrong.

    Mutations are refused when ``TRADER_CONTROL_TOKEN`` is unset in production,
    even if the caller sends an empty bearer. An unset token never authorizes
    any control route.
    """
    expected = control_token()
    if not expected:
        if mutate and _production():
            raise HTTPException(status_code=401, detail="control token unset")
        raise HTTPException(status_code=401, detail="unauthorized")
    presented = presented_bearer(authorization)
    if presented is None or not _tokens_match(presented, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


def control_actor(raw: str | None) -> str:
    if raw is None or not raw.strip():
        return "cursor"
    actor = raw.strip().lower()
    if actor not in {"cursor", "ui"}:
        raise HTTPException(status_code=400, detail="actor must be cursor or ui")
    return actor


def require_control_read(
    authorization: Annotated[str | None, Header()] = None,
    x_control_actor: Annotated[str | None, Header()] = None,
) -> str:
    authorize_control(authorization, mutate=False)
    return control_actor(x_control_actor)


def require_control_write(
    authorization: Annotated[str | None, Header()] = None,
    x_control_actor: Annotated[str | None, Header()] = None,
) -> str:
    authorize_control(authorization, mutate=True)
    return control_actor(x_control_actor)


def mask_secret_values(data: dict[str, Any]) -> dict[str, Any]:
    masked = dict(data)
    for key, value in masked.items():
        lowered = key.lower()
        if value and any(marker in lowered for marker in _SECRET_MARKERS):
            masked[key] = "***"
    return masked
