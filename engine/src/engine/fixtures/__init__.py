"""Shipped fixture worlds + the fixture seeding API (R7).

``demo_world()`` is the campaign the CLI plays out of the box; ``seed_world``
writes any harness-shaped world dict into a store; ``load_world`` resolves
``demo`` / a ``.json`` / a ``.py`` fixture for ``--world``.
"""
from .demo_world import (
    DEMO_WORLD,
    FIXTURE_MODELS,
    demo_world,
    load_world,
    seed_world,
)

__all__ = [
    "DEMO_WORLD",
    "FIXTURE_MODELS",
    "demo_world",
    "load_world",
    "seed_world",
]
