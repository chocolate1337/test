import pytest
from fastapi import HTTPException

from app.auth import require_api_key
from app.config import settings


async def test_missing_key_is_401() -> None:
    with pytest.raises(HTTPException) as exc:
        await require_api_key(None)
    assert exc.value.status_code == 401


async def test_wrong_key_is_401() -> None:
    with pytest.raises(HTTPException) as exc:
        await require_api_key("nope")
    assert exc.value.status_code == 401


async def test_valid_key_passes() -> None:
    await require_api_key(settings.api_key)
