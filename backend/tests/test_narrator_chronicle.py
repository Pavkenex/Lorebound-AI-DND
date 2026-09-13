"""Narrator continuity: the recent chronicle rides the prompt (bug repro).

The reported bug: dialogue "would not move on" — the player sat down, and
Marla told them to sit again — because the narrator prompt carried no memory
of the previous turns at all. The fix feeds the bounded tail of the
player-visible chronicle (narration / dialogue / notices) into the prompt;
these tests pin that contract.
"""
from __future__ import annotations

from app.modules.ai.providers import ProviderResult
from app.modules.narrator import prompts as pm
from app.modules.narrator.prompts import PromptContext, assemble_prompt
from app.modules.play.engine import ActEngine
from app.modules.play.session import PlaySession
from app.modules.play.state import seeded_state


class _CapturingProvider:
    """Records every prompt; replies with a fixed narrator payload."""

    model_name = "capture"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt, *, role="narrator", max_tokens=600, **kwargs):
        self.prompts.append(prompt)
        return ProviderResult(text=(
            '{"narration": "The fire pops and settles.", "npc_dialogue": ['
            '{"npc": "Marla", "line": "Mind the embers."}], "suggested_actions": []}'
        ))


def test_prompt_renders_the_recent_chronicle_newest_last():
    user = assemble_prompt(PromptContext(chronicle=[
        {"kind": "narration", "text": "You pull out a chair and sit across from Marla."},
        {"kind": "dialogue", "speaker": "Marla Voss", "text": "Then sit, and listen well."},
        {"kind": "system", "text": "❧ I sit down."},
    ])).user
    assert "[Recent chronicle" in user
    first = user.index("You pull out a chair and sit across from Marla.")
    quote = user.index('Marla Voss: "Then sit, and listen well."')
    last = user.index("❧ I sit down.")
    assert first < quote < last  # newest last; dialogue keeps its speaker
    assert user.index("[Recent chronicle") < user.index("[Player action]")


def test_chronicle_retrieval_is_capped_and_keeps_the_tail():
    entries = [{"kind": "narration", "text": f"beat {i}"}
               for i in range(pm.MAX_RETRIEVED_CHRONICLE + 4)]
    user = assemble_prompt(PromptContext(chronicle=entries)).user
    newest = f"beat {pm.MAX_RETRIEVED_CHRONICLE + 3}"
    assert newest in user and "beat 0" not in user


def test_second_turn_prompt_carries_the_first_turn_chronicle():
    st = seeded_state()  # the opening narration + Marla's line sit in the feed
    prov = _CapturingProvider()
    engine = ActEngine(PlaySession("chron-prompt", st), provider=prov)

    engine.act("I settle by the hearth and hum a lamplighter's tune")
    engine.act("I keep humming the same tune under my breath.")
    assert len(prov.prompts) == 2, "both turns must ride the narrator pipeline"

    first, second = prov.prompts
    # Turn one already knows the opening beat the player just read...
    assert "Come in from the rain" in first
    # ...and turn two carries what turn one told the player: their own words,
    # the prose, and the spoken line — the scene continues instead of restarting.
    assert "hum a lamplighter's tune" in second        # the earlier action echo
    assert "The fire pops and settles." in second      # the earlier narration
    assert "Mind the embers." in second                # the earlier spoken line
