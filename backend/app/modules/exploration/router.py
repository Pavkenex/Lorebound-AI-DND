"""Exploration HTTP surface: map + lead-graph data APIs."""
from __future__ import annotations

from fastapi import APIRouter

from app.modules.exploration.leads import seed_caravan_graph
from app.modules.exploration.map_api import get_map_data
from app.modules.exploration.regions import get_ravenford_valley

router = APIRouter(prefix="/exploration", tags=["exploration"])


@router.get("/map")
def map_data() -> dict:
    region = get_ravenford_valley()
    known = {"ravenford", "lantern-inn", "ember-hollow", "millbrook"}
    return get_map_data(region, known_ids=known, confirmed_ids={"ravenford"})


@router.get("/leads/graph")
def lead_graph() -> dict:
    return seed_caravan_graph().to_viz_data()
