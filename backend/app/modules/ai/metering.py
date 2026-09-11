"""AI cost metering & control (t_eff821f1, GDD §105).

One player action normally costs exactly one primary narration call.
Usage is measured and reported per campaign.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

#: Roles that count as the single "primary narration call" per action.
PRIMARY_ROLES = frozenset({"narrator"})
#: Expected primary calls per player action (budget norm).
CALLS_PER_ACTION = 1


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
        return {
            "campaign_id": self.campaign_id,
            "actions": self.actions,
            "calls": self.calls,
            "primary_calls": self.primary_calls,
            "primary_per_action": round(self.primary_per_action, 3),
            "within_budget": self.primary_per_action <= CALLS_PER_ACTION + 1e-9 or self.actions == 0,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
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
