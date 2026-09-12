"""Lantern-primer tutorial scenario: scripted onboarding into Missing Travelers.

Reuses fixture names (Aric / Lantern Inn / Marla) so the tutorial plays in
the same room the acceptance flows already prove.
"""
from __future__ import annotations

INTRODUCTION: dict = {
    "id": "lantern-primer",
    "title": "Lantern Primer: A Rainy Arrival",
    "pc": "Aric",
    "location": "Lantern Inn",
    "lead": "missing-travelers",
    "beats": [
        {
            "id": "arrival-in-rain",
            "title": "Arrival in the Rain",
            "text": (
                "Rain hammers the shutters as Aric pushes open the door of the "
                "Lantern Inn, cloak dripping. Warmth, peat smoke, and the low "
                "murmur of travelers greet him."
            ),
        },
        {
            "id": "meet-marla",
            "title": "Meet Marla",
            "text": (
                "Marla, the innkeeper, polishes a tankard and sizes up Aric. "
                "'Rooms cost coin, stories cost less,' she says. 'What brings "
                "you to Ravenford in this weather?'"
            ),
            "npc": "Marla",
        },
        {
            "id": "first-check-explained",
            "title": "Your First Check, Explained",
            "text": (
                "When Aric tries something risky, roll a skill check: roll a d20, "
                "add the skill bonus, and beat the difficulty. Marla suggests a "
                "Charm check to loosen tongues about the road."
            ),
            "check": {"skill": "Charm", "difficulty": 10, "example": "d20 + Charm vs 10"},
        },
        {
            "id": "inspect-room",
            "title": "Inspect the Common Room",
            "text": (
                "Aric looks around: tables scarred by knives, a hearth hissing "
                "with peat, and a notice board bearing a plea about missing "
                "travelers. An Investigation check (d20 vs 10) spots muddy "
                "bootprints heading toward the Northern Road."
            ),
            "check": {"skill": "Investigation", "difficulty": 10, "example": "d20 + Investigation vs 10"},
        },
        {
            "id": "mini-choice",
            "title": "A Mini-Choice: Two Valid Approaches",
            "text": (
                "Marla mentions two travelers bound for the Old Monastery who "
                "never arrived. Aric can press her with questions (Charm) or "
                "slip behind the bar to read her guest ledger (Stealth). Both "
                "approaches are valid and lead onward."
            ),
            "choices": [
                {"id": "ask", "label": "Ask Marla directly", "check": "Charm"},
                {"id": "snoop", "label": "Sneak a look at the ledger", "check": "Stealth"},
            ],
        },
        {
            "id": "hook-missing-travelers",
            "title": "Hook: The Missing Travelers",
            "text": (
                "Whether by word or by ledger, Aric learns the truth: two "
                "travelers left the Lantern Inn for the Old Monastery in the "
                "rain and never arrived. Marla marks the Northern Road on his "
                "map — the Missing Travelers lead begins."
            ),
            "lead": "missing-travelers",
        },
    ],
}
