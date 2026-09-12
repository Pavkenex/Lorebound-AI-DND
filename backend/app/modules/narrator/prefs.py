"""Content preferences: player-facing tone boundaries for narration (GDD §25).

Violence / horror / romance / language each take off | reduced | standard;
nsfw is an explicit opt-in (default False). :meth:`ContentPrefs.describe_for_prompt`
renders a short directive string the narrator prompt assembly appends.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Level = Literal["off", "reduced", "standard"]

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

    def describe_for_prompt(self) -> str:
        """Short directive string for narrator prompt assembly."""
        parts = [
            f"violence: {_LEVEL_PHRASE['violence'][self.violence]}",
            f"horror: {_LEVEL_PHRASE['horror'][self.horror]}",
            f"romance: {_LEVEL_PHRASE['romance'][self.romance]}",
            f"language: {_LEVEL_PHRASE['language'][self.language]}",
        ]
        if self.nsfw:
            parts.append(
                "mature themes permitted for consenting adults; "
                "still never depict disallowed content such as underage "
                "or non-consensual sexual content"
            )
        else:
            parts.append("no explicit sexual content")
        return "Content boundaries — " + "; ".join(parts) + "."
