import pytest

from arrow_assistant import usage


@pytest.fixture(autouse=True)
def _isolated_usage(tmp_path, monkeypatch):
    """Every test gets its own usage.json, so nothing touches %APPDATA%."""
    for k in list(__import__("os").environ):
        if k.startswith("ARROW_DAILY_") or k == "ARROW_USAGE_ALERTS":
            monkeypatch.delenv(k, raising=False)
    t = usage.UsageTracker(path=str(tmp_path / "usage.json"))
    usage.set_tracker(t)
    yield t
    usage.set_tracker(None)
