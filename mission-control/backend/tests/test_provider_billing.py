"""Tests for multi-provider billing monitoring.

Tests the provider balance fetching, DB storage, and API endpoints
for DeepSeek, Moonshot, Tavily (and existing OpenRouter).
"""

import json
import os
import sys
import time
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault(
    "DATABASE_URL",
    f"postgresql://missioncontrol:{os.environ.get('POSTGRES_PASSWORD', 'testpassword')}@localhost:5432/missioncontrol",
)
os.environ.setdefault("API_KEY", os.environ.get("API_KEY", "test-api-key"))
os.environ.setdefault("OPENCLAW_DIR", "/tmp")
os.environ.setdefault("POLYBOT_DATA_DIR", "/tmp")


@pytest.fixture(scope="module")
def app():
    from main import app, init_db
    init_db()
    return app


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── Provider fetch function tests ──────────────────────────────────────────


class TestFetchDeepSeekBalance:
    """Test _fetch_deepseek_balance()."""

    def test_returns_balance_on_success(self):
        from app.budget_helpers import _fetch_deepseek_balance_inner
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "is_available": True,
            "balance_infos": [
                {"currency": "USD", "total_balance": "9.50",
                 "granted_balance": "0.00", "topped_up_balance": "9.50"}
            ]
        }).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        with patch("app.budget_helpers.urlopen_", return_value=mock_response):
            result = _fetch_deepseek_balance_inner()
        assert result["provider"] == "deepseek"
        assert result["balance_usd"] == 9.50
        assert "error" not in result

    def test_returns_error_on_failure(self):
        from app.budget_helpers import _fetch_deepseek_balance_inner
        with patch("app.budget_helpers.urlopen_", side_effect=Exception("timeout")):
            result = _fetch_deepseek_balance_inner()
        assert "error" in result
        assert result["provider"] == "deepseek"


class TestFetchMoonshotBalance:
    """Test _fetch_moonshot_balance()."""

    def test_returns_balance_on_success(self):
        from app.budget_helpers import _fetch_moonshot_balance_inner
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "data": {
                "available_balance": 4.20,
                "voucher_balance": 1.00,
                "cash_balance": 3.20,
            }
        }).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        with patch("app.budget_helpers.urlopen_", return_value=mock_response):
            result = _fetch_moonshot_balance_inner()
        assert result["provider"] == "moonshot"
        assert result["balance_usd"] == 4.20
        assert "error" not in result

    def test_returns_error_on_failure(self):
        from app.budget_helpers import _fetch_moonshot_balance_inner
        with patch("app.budget_helpers.urlopen_", side_effect=Exception("connection refused")):
            result = _fetch_moonshot_balance_inner()
        assert "error" in result
        assert result["provider"] == "moonshot"


class TestFetchTavilyUsage:
    """Test _fetch_tavily_usage()."""

    def test_returns_usage_on_success(self):
        from app.budget_helpers import _fetch_tavily_usage_inner
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "key": {"usage": 150, "limit": 1000},
            "account": {"current_plan": "free"}
        }).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        with patch("app.budget_helpers.urlopen_", return_value=mock_response):
            result = _fetch_tavily_usage_inner()
        assert result["provider"] == "tavily"
        assert result["used"] == 150
        assert result["limit"] == 1000
        assert "error" not in result

    def test_returns_error_on_failure(self):
        from app.budget_helpers import _fetch_tavily_usage_inner
        with patch("app.budget_helpers.urlopen_", side_effect=Exception("401")):
            result = _fetch_tavily_usage_inner()
        assert "error" in result
        assert result["provider"] == "tavily"


# ── Provider balances table tests ──────────────────────────────────────────


class TestProviderBalancesTable:
    """Test that the provider_balances table is created and usable."""

    def test_table_exists(self, app):
        from main import _pool
        conn = _pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_name = 'provider_balances'
                    )
                """)
                assert cur.fetchone()[0], "provider_balances table should exist"
        finally:
            _pool.putconn(conn)

    def test_insert_and_query(self, app):
        from main import _pool
        conn = _pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO provider_balances
                        (provider, balance_usd, used_credits, total_credits, raw_json)
                    VALUES ('test-provider', 42.00, 10, 100, '{}')
                    RETURNING id
                """)
                row_id = cur.fetchone()[0]
                assert row_id is not None
                cur.execute(
                    "DELETE FROM provider_balances WHERE provider = 'test-provider'"
                )
            conn.commit()
        finally:
            _pool.putconn(conn)


# ── API endpoint tests ─────────────────────────────────────────────────────


class TestBillingEndpoint:
    """Test GET /api/billing endpoint."""

    @pytest.mark.anyio
    async def test_billing_returns_all_providers(self, client):
        resp = await client.get("/api/billing")
        assert resp.status_code == 200
        data = resp.json()
        assert "providers" in data
        assert isinstance(data["providers"], list)

    @pytest.mark.anyio
    async def test_billing_provider_fields(self, client):
        resp = await client.get("/api/billing")
        data = resp.json()
        for p in data["providers"]:
            assert "provider" in p
            assert "status" in p  # "ok", "error", "no_key"

    @pytest.mark.anyio
    async def test_billing_history_endpoint(self, client):
        resp = await client.get("/api/billing/history?days=7")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)


class TestBillingAlerts:
    """Test billing alert thresholds."""

    def test_low_balance_alert(self):
        """Provider with balance below threshold should trigger alert."""
        from app.budget_helpers import _check_balance_alert
        assert _check_balance_alert(0.50, 10.0) is True   # 5% remaining
        assert _check_balance_alert(5.00, 10.0) is False  # 50% remaining

    def test_high_usage_alert(self):
        """Provider with usage > 80% of limit should trigger alert."""
        from app.budget_helpers import _check_usage_alert
        assert _check_usage_alert(900, 1000) is True   # 90% used
        assert _check_usage_alert(500, 1000) is False  # 50% used
