"""Journal lead-graph visualisation data API (GDD §74).

Interconnected node view of leads; any node opens details. The graph updates
as clues are confirmed or invalidated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class LeadStatus(str, Enum):
    SUSPECTED = "suspected"
    CONFIRMED = "confirmed"
    INVALIDATED = "invalidated"


@dataclass
class LeadNode:
    id: str
    label: str
    detail: str = ""
    status: LeadStatus = LeadStatus.SUSPECTED


@dataclass
class LeadGraph:
    nodes: dict[str, LeadNode] = field(default_factory=dict)
    edges: list[tuple[str, str, str]] = field(default_factory=list)  # (from, to, relation)

    def add_lead(self, node: LeadNode) -> None:
        self.nodes[node.id] = node

    def link(self, from_id: str, to_id: str, relation: str) -> None:
        if from_id not in self.nodes or to_id not in self.nodes:
            raise KeyError("Both lead nodes must exist before linking")
        self.edges.append((from_id, to_id, relation))

    def confirm_lead(self, lead_id: str) -> None:
        self.nodes[lead_id].status = LeadStatus.CONFIRMED

    def invalidate_lead(self, lead_id: str) -> None:
        self.nodes[lead_id].status = LeadStatus.INVALIDATED

    def to_viz_data(self) -> dict:
        return {
            "nodes": [
                {"id": n.id, "label": n.label, "detail": n.detail, "status": n.status.value}
                for n in self.nodes.values()
            ],
            "edges": [
                {"from": a, "to": b, "relation": r} for a, b, r in self.edges
            ],
        }


def seed_caravan_graph() -> LeadGraph:
    """Missing Caravan -> Destroyed Wagon -> Silver Powder -> Merchant Guild
    -> Smugglers / Old Monastery."""
    g = LeadGraph()
    for lid, label in [
        ("missing-caravan", "Missing Caravan"),
        ("destroyed-wagon", "Destroyed Wagon"),
        ("silver-powder", "Silver Powder"),
        ("merchant-guild", "Merchant Guild"),
        ("smugglers", "Smugglers"),
        ("old-monastery", "Old Monastery"),
    ]:
        g.add_lead(LeadNode(lid, label))
    g.link("missing-caravan", "destroyed-wagon", "found at")
    g.link("destroyed-wagon", "silver-powder", "carried")
    g.link("silver-powder", "merchant-guild", "consigned to")
    g.link("merchant-guild", "smugglers", "possibly employs")
    g.link("smugglers", "old-monastery", "holed up in")
    return g
