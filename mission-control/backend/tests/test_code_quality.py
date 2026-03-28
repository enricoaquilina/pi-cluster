"""Code quality tests: connection pooling, enum usage, no raw DB connects."""

import ast
import os


def _app_source_files():
    """Return all .py files in the app/ package."""
    app_dir = os.path.join(os.path.dirname(__file__), "..", "app")
    files = []
    for root, _dirs, filenames in os.walk(app_dir):
        for fn in filenames:
            if fn.endswith(".py"):
                files.append(os.path.join(root, fn))
    return files


def test_no_raw_psycopg2_connect():
    """All DB access should use _pool or context manager, not raw psycopg2.connect().close()."""
    for filepath in _app_source_files():
        content = open(filepath).read()
        lines = content.splitlines()
        bad = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if "psycopg2.connect(" in stripped and "ThreadedConnectionPool" not in stripped:
                # Allow 'with psycopg2.connect(...)' (context manager — handles close)
                if stripped.startswith("with "):
                    continue
                bad.append((i + 1, stripped, filepath))
        assert not bad, (
            f"Found {len(bad)} raw psycopg2.connect() call(s) — use _pool.getconn() instead:\n"
            + "\n".join(f"  {fp} line {n}: {line}" for n, line, fp in bad)
        )


def test_db_functions_use_try_finally():
    """Functions that get pool connections must use try/finally to return them."""
    target_funcs = {"_heartbeat_sweep", "_is_node_dispatchable", "_log_dispatch",
                    "init_db", "_budget_snapshot", "_node_snapshot"}
    found = set()
    for filepath in _app_source_files():
        tree = ast.parse(open(filepath).read())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in target_funcs:
                found.add(node.name)
                has_finally = any(
                    isinstance(n, ast.Try) and n.finalbody
                    for n in ast.walk(node)
                )
                assert has_finally, f"{node.name} in {filepath} uses DB connection without try/finally — pool connections will leak"
    assert found == target_funcs, f"Missing functions: {target_funcs - found}"


def test_node_status_enum_used_in_models():
    """Pydantic models should use NodeStatus enum, not bare str for status."""
    from main import NodeCreate, NodeStatus
    default = NodeCreate.model_fields["status"].default
    assert isinstance(default, NodeStatus), f"NodeCreate.status default is {type(default)}, expected NodeStatus"


def test_service_status_enum_exists():
    """ServiceStatus and AlertStatus enums should exist."""
    from main import ServiceStatus, AlertStatus
    assert "up" in [s.value for s in ServiceStatus]
    assert "recovered" in [s.value for s in AlertStatus]


def test_event_bus_has_subscriber_count():
    """EventBus should expose subscriber_count property, not _subscribers."""
    from main import EventBus
    bus = EventBus()
    assert hasattr(bus, "subscriber_count"), "EventBus missing subscriber_count property"
    assert bus.subscriber_count == 0


def test_pool_has_closeall():
    """Connection pool should have closeall for shutdown lifecycle."""
    from main import _pool
    assert hasattr(_pool, "closeall"), "Pool missing closeall method"
