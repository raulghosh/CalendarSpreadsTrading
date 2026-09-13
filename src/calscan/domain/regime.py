"""Vol regime classification → playbook. Build plan §5.6."""

from __future__ import annotations

from dataclasses import dataclass

from calscan.settings import RegimeConfig


@dataclass(frozen=True, slots=True)
class RegimeInputs:
    ivts: float
    slope_z: float
    slope_z_top: float
    vix: float
    vix_median_60d: float
    rv10: float
    breakeven_daily_sigma: float
    events_in_window: bool
    # Playbook B confirmation signals (front IV lower high, curve flattening, SPY up day).
    front_iv_lower_high_2d: bool = False
    vix9d_below_vix: bool = False
    spy_up_day: bool = False


@dataclass(frozen=True, slots=True)
class RegimeResult:
    regime: str
    playbook: str
    note: str | None
    tags: tuple[str, ...]
    confirmed: bool | None  # backwardation confirmation signals; None outside playbook B


def classify(inputs: RegimeInputs, cfg: RegimeConfig) -> RegimeResult:
    regime: str
    playbook: str
    note: str | None

    if inputs.ivts >= cfg.ivts_backwardation_strong:
        regime, playbook, note = "BACKWARDATION_STRONG", "B", "wait for confirmation"
    elif inputs.ivts >= cfg.ivts_backwardation:
        regime, playbook, note = "BACKWARDATION", "B", "early — confirmation required"
    elif inputs.ivts <= cfg.ivts_carry_max and inputs.slope_z < inputs.slope_z_top:
        regime, playbook, note = "CONTANGO_CARRY", "A", None
    elif inputs.ivts <= cfg.ivts_carry_max:
        regime, playbook, note = "CONTANGO_STEEP", "NONE", "forward too rich"
    else:
        regime, playbook, note = "TRANSITION", "NONE", None

    tags = ("EVENT",) if inputs.events_in_window else ()

    confirmed = None
    if playbook == "B":
        confirmed = (
            inputs.front_iv_lower_high_2d and inputs.vix9d_below_vix and inputs.spy_up_day
        )

    return RegimeResult(regime=regime, playbook=playbook, note=note, tags=tags, confirmed=confirmed)
