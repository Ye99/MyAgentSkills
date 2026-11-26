import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import rename_folder_by_ai_itinerary as mod  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_opencode(monkeypatch):
    """Keep every test independent of the real opencode CLI.

    The "is opencode installed" answer is cached in a module global, so one
    test's answer used to leak into the next. A real `opencode serve` must
    never start from a test, so the default server starter refuses; tests
    that need servers patch in a fake.
    """
    monkeypatch.setattr(mod, "_OPENCODE_AVAILABLE", None)

    def _refuse(*_args, **_kwargs):
        raise RuntimeError("tests must not start a real opencode server")

    monkeypatch.setattr(mod, "start_opencode_server", _refuse)
