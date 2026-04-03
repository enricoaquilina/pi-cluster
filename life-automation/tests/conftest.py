"""Shared pytest configuration for life-automation tests."""


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "local_only: test requires local tools (QMD, specific timezone, etc.)"
    )
