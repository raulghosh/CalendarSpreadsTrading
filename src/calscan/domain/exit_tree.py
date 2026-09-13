"""Position exit decision tree. Build plan §5.9. Ordered, first match wins."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from calscan.settings import PlaybookTargets, RegimeConfig

Action = Literal["CLOSE", "CLOSE_OR_SCALE", "HEDGE", "WING_OR_REDUCE", "HOLD"]


@dataclass(frozen=True, slots=True)
class ExitInputs:
    dte1: int
    spot: float
    strike: float
    sigma_entry_pts: float  # frozen at entry
    playbook: Literal["A", "B"]
    ivts: float
    pnl_pct: float
    net_delta: float
    event_in_front_window_within_3_days: bool


@dataclass(frozen=True, slots=True)
class ExitVerdict:
    action: Action
    reason: str


def decide_exit(
    inputs: ExitInputs, targets: PlaybookTargets, regime_cfg: RegimeConfig, delta_band: float
) -> ExitVerdict:
    if inputs.dte1 <= targets.time_stop_dte:
        return ExitVerdict("CLOSE", "time stop")

    distance_sigma = abs(inputs.spot - inputs.strike) / inputs.sigma_entry_pts
    if distance_sigma > targets.distance_stop_sigma:
        return ExitVerdict("CLOSE", "distance stop")

    if inputs.playbook == "A" and inputs.ivts >= regime_cfg.ivts_backwardation:
        return ExitVerdict("CLOSE", "curve inverted vs carry thesis")
    if inputs.playbook == "B" and inputs.ivts <= regime_cfg.ivts_carry_max:
        return ExitVerdict("CLOSE", "curve normalized; thesis realized")

    if inputs.pnl_pct <= targets.stop_pct:
        return ExitVerdict("CLOSE", "P&L stop")

    if inputs.pnl_pct >= targets.profit_pct:
        return ExitVerdict("CLOSE_OR_SCALE", "target hit")

    if abs(inputs.net_delta) > delta_band:
        return ExitVerdict("HEDGE", "delta band breached")

    if inputs.event_in_front_window_within_3_days:
        return ExitVerdict("WING_OR_REDUCE", "event inside front window")

    return ExitVerdict("HOLD", "no trigger")
