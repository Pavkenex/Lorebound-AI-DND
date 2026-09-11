"""Progression HTTP surface: skills screen data API."""
from __future__ import annotations

from fastapi import APIRouter

from app.modules.progression.screen import build_skills_screen
from app.modules.progression.skills import SkillProgress, format_skill_display

router = APIRouter(prefix="/skills", tags=["progression"])


@router.get("/screen")
def skills_screen() -> dict:
    demo = [SkillProgress("Swordsmanship", 1420), SkillProgress("Lockpicking", 320)]
    return build_skills_screen(demo)


@router.get("/display")
def skill_display(name: str = "Swordsmanship", xp: int = 1420) -> dict:
    return {"display": format_skill_display(name, xp)}
