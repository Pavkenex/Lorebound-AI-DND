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


@pytest.fixture()
def recording_narrator(monkeypatch):
    """A connected model on the play path that writes prose and keeps prompts.

    The P14 contract is "every story word is the model's, written from the
    engine's facts", so a test that wants to inspect the promise (what the
    engine handed the narrator) or prove the delivery (the player-visible text
    came from the provider) injects this double instead of the stub, at the
    same seam production resolves a provider from.
    """
    import app.modules.play.router as play_router
    from tests.narrator_fake import RecordingNarrator

    narrator = RecordingNarrator()
    monkeypatch.setattr(play_router, "resolve_provider", lambda db, user_id: narrator)
    return narrator
