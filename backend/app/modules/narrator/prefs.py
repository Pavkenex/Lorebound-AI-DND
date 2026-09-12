"""Content preferences: player-facing tone boundaries for narration (GDD §25).

Violence / horror / romance / language each take off | reduced | standard;
nsfw is the explicit master switch (default False): when on, every limit is
lifted — the per-axis caps are superseded, explicit adult content does not
fade to black, and the one standing hard exclusion is sexual content
involving minors. :meth:`ContentPrefs.describe_for_prompt` renders the
directive string the narrator prompt assembly appends.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator

Level = Literal["off", "reduced", "standard"]

#: Legacy/client-side vocabulary mapped onto the canonical levels. The first
#: frontend build shipped "low" / "clean" / "mild"; without this aliasing the
#: whole prefs payload failed validation and every boundary (NSFW included)
#: was silently dropped server-side.
_LEVEL_ALIASES: dict[str, str] = {"low": "reduced", "clean": "reduced", "mild": "reduced"}

_LEVEL_PHRASE: dict[str, dict[str, str]] = {
    "violence": {
        "off": "no graphic violence",
        "reduced": "mild, non-graphic violence only",
        "standard": "standard fantasy violence",
    },
    "horror": {
        "off": "no horror elements",
        "reduced": "mild spooky tension only, nothing graphic",
        "standard": "standard fantasy horror",
    },
    "romance": {
        "off": "no romantic content",
        "reduced": "chaste romance only, fade to black",
        "standard": "standard romantic themes",
    },
    "language": {
        "off": "no profanity",
        "reduced": "mild language only",
        "standard": "occasional strong language permitted",
    },
}


class ContentPrefs(BaseModel):
    violence: Level = "standard"
    horror: Level = "standard"
    romance: Level = "standard"
    language: Level = "standard"
    nsfw: bool = False

    @field_validator("violence", "horror", "romance", "language", mode="before")
    @classmethod
    def _normalize_level(cls, value: object) -> object:
        if isinstance(value, str):
            cleaned = value.strip().lower()
            return _LEVEL_ALIASES.get(cleaned, cleaned)
        return value

    def describe_for_prompt(self) -> str:
        """Short directive string for narrator prompt assembly.

        NSFW is the master switch: when the player opts in, every content
        limit is lifted (explicit adult content, graphic violence, strong
        language, no fade-to-black) and the per-axis caps above are
        superseded. The one standing hard exclusion is sexual content
        involving minors.
        """
        if self.nsfw:
            return (
                "Content boundaries — uncensored mode (the player has opted in): "
                "all content limits are lifted. Explicit sexual content between "
                "adults is permitted and fully described, graphic violence and "
                "strong language are permitted, and nothing fades to black. "
                "Hard exclusion that always stands: never sexual content "
                "involving minors."
            )
        parts = [
            f"violence: {_LEVEL_PHRASE['violence'][self.violence]}",
            f"horror: {_LEVEL_PHRASE['horror'][self.horror]}",
            f"romance: {_LEVEL_PHRASE['romance'][self.romance]}",
            f"language: {_LEVEL_PHRASE['language'][self.language]}",
            "no explicit sexual content",
        ]
        return "Content boundaries — " + "; ".join(parts) + "."
