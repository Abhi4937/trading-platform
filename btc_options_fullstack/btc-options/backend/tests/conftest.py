"""Pytest configuration for backend tests.

Registers the `slow` marker used by historical-validation tests against the
M7 dataset (`tests/test_m7_historical_validation.py`). Slow tests are
excluded from the default run and opted into with `-m slow`.
"""
import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "slow: full-dataset historical validations against the M7 parquet "
        "(use `pytest -m slow`); excluded from default run via `-m 'not slow'`.",
    )
    config.addinivalue_line(
        "markers",
        "benchmark: manual performance benchmarks; skipped by default "
        "(use `pytest -m benchmark` to run).",
    )


def pytest_collection_modifyitems(config, items):
    """Skip @pytest.mark.slow tests unless the user opts in with `-m slow`.
    Without this, `pytest` would run them by default which makes the suite
    take 5+ minutes."""
    marker_expr = config.getoption("-m") or ""
    if "slow" in marker_expr:
        return  # User explicitly asked for slow tests
    skip_slow = pytest.mark.skip(reason="opt in with `pytest -m slow`")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)
