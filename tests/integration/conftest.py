import os

import pytest


@pytest.fixture(scope="session")
def rabbit_url() -> str:
    url = os.environ.get("RABBITMQ_URL")
    if not url:
        pytest.skip("задайте RABBITMQ_URL (docker compose / CI services)")
    return url


@pytest.fixture(scope="session")
def postgres_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("задайте DATABASE_URL (docker compose / CI services)")
    return url
