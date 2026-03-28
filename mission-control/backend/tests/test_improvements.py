"""Tests for Phase 1 improvements: middleware, timeout cap, rename, code quality."""

import ast
import logging
import os

import pytest
from pydantic import ValidationError


async def test_request_logging_middleware(client, caplog):
    """Requests to API endpoints are logged with method/path/status."""
    with caplog.at_level(logging.INFO, logger="mission-control"):
        resp = await client.get("/api/tasks")
        assert resp.status_code == 200
    assert any("GET /api/tasks 200" in rec.message for rec in caplog.records)


async def test_health_not_logged(client, caplog):
    """Health endpoint is excluded from request logging."""
    with caplog.at_level(logging.INFO, logger="mission-control"):
        await client.get("/health")
    assert not any("GET /health" in rec.message for rec in caplog.records)


def test_dispatch_timeout_rejects_over_120():
    """DispatchRequest rejects timeout > 120s."""
    from main import DispatchRequest
    with pytest.raises(ValidationError):
        DispatchRequest(persona="test", prompt="test", timeout=121)


def test_dispatch_timeout_accepts_120():
    """DispatchRequest accepts timeout = 120s."""
    from main import DispatchRequest
    req = DispatchRequest(persona="test", prompt="test", timeout=120)
    assert req.timeout == 120


def test_row_to_dict_exists():
    """row_to_task was renamed to row_to_dict."""
    from main import row_to_dict
    result = row_to_dict(("a", "b"), ["x", "y"])
    assert result == {"x": "a", "y": "b"}


def test_row_to_task_gone():
    """Old row_to_task name should not exist."""
    import main
    assert not hasattr(main, "row_to_task"), "row_to_task should be renamed to row_to_dict"


def test_no_dead_copybot_pass_loop():
    """copybot_traders should not contain a dead pass loop."""
    # After module split, function lives in app/routes/trading.py
    for filename in ["main.py", os.path.join("app", "routes", "trading.py")]:
        candidate = os.path.join(os.path.dirname(__file__), "..", filename)
        if os.path.exists(candidate):
            path = candidate
            break
    tree = ast.parse(open(path).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "copybot_traders":
            for child in ast.walk(node):
                if isinstance(child, ast.Pass):
                    pytest.fail("copybot_traders still contains a dead 'pass' statement")


def test_lifespan_calls_closeall():
    """Lifespan shutdown should call _pool.closeall()."""
    # After module split, lifespan lives in app/__init__.py
    for filename in ["main.py", os.path.join("app", "__init__.py")]:
        path = os.path.join(os.path.dirname(__file__), "..", filename)
        if not os.path.exists(path):
            continue
        tree = ast.parse(open(path).read())
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan":
                src = ast.dump(node)
                assert "closeall" in src, "lifespan missing _pool.closeall()"
                return
    pytest.fail("lifespan function not found in main.py or app/__init__.py")
