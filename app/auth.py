import secrets
from typing import Annotated

from fastapi import Header, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings


def api_key_is_valid(supplied: str | None) -> bool:
    if supplied is None:
        return False
    expected = settings.api_key
    if len(supplied) != len(expected):
        return False
    return secrets.compare_digest(supplied, expected)


async def require_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    if not api_key_is_valid(x_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Нет или неверный X-API-Key",
        )


class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if not api_key_is_valid(request.headers.get("X-API-Key")):
            return JSONResponse(
                {"detail": "Нет или неверный X-API-Key"},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        return await call_next(request)
