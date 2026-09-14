import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def stub_play_provider(monkeypatch):
    """Internal test plumbing for the player path (P12 — the stub, injected).

    The story game is not playable without a connected model: ``/act`` refuses
    an unset/retired-``stub`` connection with ``400 connect_your_ai`` before the
    engine. The suite still plays through the real engine with the
    deterministic stub, injected at the same seam production resolves a
    provider from — which is exactly what "the built-in storyteller survives
    only as internal test plumbing" means.

    Modules that exercise ``/act`` opt in at module level::

        pytestmark = pytest.mark.usefixtures("stub_play_provider")

    A test that asserts the refusal itself must not opt in (see
    ``tests/test_ai_settings_api.py``).
    """
    import app.modules.play.router as play_router
    from app.modules.ai.providers import StubProvider

    monkeypatch.setattr(
        play_router, "resolve_provider", lambda db, user_id: StubProvider()
    )
