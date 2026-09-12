"""AI cost metering & control (t_eff821f1, GDD §105).

One player action normally costs exactly one primary narration call.
Usage is measured and reported per campaign.
"""
from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass, field

#: Roles that count as the single "primary narration call" per action.
PRIMARY_ROLES = frozenset({"narrator"})
#: Expected primary calls per player action (budget norm).
CALLS_PER_ACTION = 1


def _cost_rates() -> tuple[float, float]:
    """USD per 1K tokens (prompt, completion) from the environment.

    Defaults to 0 — the stub provider genuinely costs nothing, and the headers
    must not invent a price for it.
    """
    def _read(name: str) -> float:
        try:
            return float(os.environ.get(name) or 0)
        except ValueError:
            return 0.0

    return _read("AI_COST_PROMPT_PER_1K"), _read("AI_COST_COMPLETION_PER_1K")


@dataclass
class CampaignMeter:
    campaign_id: str
    calls: int = 0
    primary_calls: int = 0
    actions: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    by_role: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def record(self, role: str, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        self.calls += 1
        self.by_role[role] += 1
        if role in PRIMARY_ROLES:
            self.primary_calls += 1
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens

    def record_action(self) -> None:
        self.actions += 1

    @property
    def primary_per_action(self) -> float:
        if not self.actions:
            return 0.0
        return self.primary_calls / self.actions

    def report(self) -> dict:
        prompt_rate, completion_rate = _cost_rates()
        cost = (self.prompt_tokens / 1000.0) * prompt_rate + (
            self.completion_tokens / 1000.0
        ) * completion_rate
        return {
            "campaign_id": self.campaign_id,
            "actions": self.actions,
            "calls": self.calls,
            "primary_calls": self.primary_calls,
            "primary_per_action": round(self.primary_per_action, 3),
            "within_budget": self.primary_per_action <= CALLS_PER_ACTION + 1e-9 or self.actions == 0,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": round(cost, 6),
            "by_role": dict(self.by_role),
        }


class MeterRegistry:
    """Per-campaign meters (in-memory; persistence is the history module's job)."""

    def __init__(self) -> None:
        self._meters: dict[str, CampaignMeter] = {}

    def for_campaign(self, campaign_id: str) -> CampaignMeter:
        if campaign_id not in self._meters:
            self._meters[campaign_id] = CampaignMeter(campaign_id=campaign_id)
        return self._meters[campaign_id]

    def record(self, campaign_id: str, role: str,
               prompt_tokens: int = 0, completion_tokens: int = 0) -> CampaignMeter:
        meter = self.for_campaign(campaign_id)
        meter.record(role, prompt_tokens, completion_tokens)
        return meter

    def report(self, campaign_id: str) -> dict:
        return self.for_campaign(campaign_id).report()


registry = MeterRegistry()
