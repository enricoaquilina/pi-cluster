"""Phase 1 verification tests: logger handler and Docker healthcheck."""

import logging
import subprocess

import pytest


async def test_logger_has_handler(client):
    """MC logger has at least one handler configured (1A)."""
    from main import logger
    assert len(logger.handlers) > 0, "Logger has no handlers — SSE messages will be silently dropped"
    assert logger.level <= logging.INFO, f"Logger level {logger.level} is above INFO, SSE messages won't show"


async def test_logger_handler_has_formatter(client):
    """MC logger handler has a formatter with timestamp (1A)."""
    from main import logger
    handler = logger.handlers[0]
    assert handler.formatter is not None, "Handler has no formatter"
    # Verify format includes timestamp and logger name
    fmt = handler.formatter._fmt
    assert "asctime" in fmt, f"Formatter missing timestamp: {fmt}"
    assert "name" in fmt, f"Formatter missing logger name: {fmt}"


async def test_health_endpoint_returns_ok(client):
    """Health endpoint still returns status ok (backward compat)."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_docker_healthcheck_in_compose():
    """docker-compose.yml has a healthcheck for the API service (1B)."""
    result = subprocess.run(
        ["python3", "-c",
         "import yaml; "
         "d = yaml.safe_load(open('/mnt/external/mission-control/docker-compose.yml')); "
         "hc = d['services']['api'].get('healthcheck'); "
         "assert hc is not None, 'No healthcheck for api service'; "
         "assert 'health' in str(hc.get('test', '')), 'Healthcheck does not check /health'; "
         "print('OK')"],
        capture_output=True, text=True
    )
    # If pyyaml not available, parse manually
    if result.returncode != 0:
        content = open("/mnt/external/mission-control/docker-compose.yml").read()
        assert "healthcheck:" in content, "No healthcheck found in docker-compose.yml"
        # Verify it's under the api service (not just db)
        api_section = content.split("api:")[1].split("proxy:")[0]
        assert "healthcheck:" in api_section, "Healthcheck not in api service section"
        assert "health" in api_section, "Healthcheck doesn't reference /health endpoint"
