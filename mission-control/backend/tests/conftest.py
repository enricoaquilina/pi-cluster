"""Shared fixtures for Mission Control tests."""

import asyncio
import os
import sys

import psycopg2
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Ensure backend module is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Set required env vars before importing app
# Real credentials are provided via environment (CI or local .env); defaults are test-only values
os.environ.setdefault(
    "DATABASE_URL",
    f"postgresql://missioncontrol:{os.environ.get('POSTGRES_PASSWORD', 'testpassword')}@localhost:5432/missioncontrol",
)
os.environ.setdefault("API_KEY", os.environ.get("API_KEY", "test-api-key"))
os.environ.setdefault("OPENCLAW_DIR", "/tmp")
os.environ.setdefault("POLYBOT_DATA_DIR", "/tmp")


@pytest.fixture(scope="session")
def app():
    from main import app, event_bus
    loop = asyncio.new_event_loop()
    event_bus.set_loop(loop)
    loop.close()
    return app


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def api_key():
    return os.environ["API_KEY"]


@pytest.fixture
def auth_headers(api_key):
    return {"X-Api-Key": api_key}


@pytest.fixture
def event_bus_instance():
    from main import event_bus
    return event_bus


@pytest_asyncio.fixture
async def event_queue(event_bus_instance):
    """Subscribe to EventBus, yield queue, unsubscribe on teardown."""
    q = event_bus_instance.subscribe()
    yield q
    event_bus_instance.unsubscribe(q)


TEST_NODE_NAMES = ["test-sse-node", "regression-test-node"]
TEST_SERVICE_NAMES = ["test-sse-svc"]


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_data():
    """Delete test nodes, services, and alerts after all tests complete."""
    yield
    db_url = os.environ["DATABASE_URL"]
    conn = psycopg2.connect(db_url)
    with conn.cursor() as cur:
        for name in TEST_NODE_NAMES:
            cur.execute("DELETE FROM nodes WHERE name = %s", (name,))
        for svc in TEST_SERVICE_NAMES:
            cur.execute("DELETE FROM service_checks WHERE service = %s", (svc,))
            cur.execute("DELETE FROM service_alerts WHERE service = %s", (svc,))
        cur.execute("DELETE FROM dispatch_log WHERE persona = 'test'")
    conn.commit()
    conn.close()
