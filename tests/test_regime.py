from calscan.domain.regime import RegimeInputs, classify
from calscan.settings import RegimeConfig

CFG = RegimeConfig(
    ivts_carry_max=0.92,
    ivts_backwardation=1.00,
    ivts_backwardation_strong=1.05,
    slope_z_window_days=252,
    slope_z_avoid_top_pct=0.90,
    vix_lookback_median_days=60,
    rv_window_short=10,
    rv_window_long=20,
)


def _inputs(**overrides: object) -> RegimeInputs:
    base: dict[str, object] = dict(
        ivts=0.9,
        slope_z=0.0,
        slope_z_top=1.28,
        vix=15.0,
        vix_median_60d=16.0,
        rv10=0.12,
        breakeven_daily_sigma=0.01,
        events_in_window=False,
    )
    base.update(overrides)
    return RegimeInputs(**base)  # type: ignore[arg-type]


def test_backwardation_strong() -> None:
    result = classify(_inputs(ivts=1.10), CFG)
    assert result.regime == "BACKWARDATION_STRONG"
    assert result.playbook == "B"


def test_backwardation_early() -> None:
    result = classify(_inputs(ivts=1.02), CFG)
    assert result.regime == "BACKWARDATION"
    assert result.playbook == "B"


def test_contango_carry_below_top_decile() -> None:
    result = classify(_inputs(ivts=0.85, slope_z=0.5, slope_z_top=1.28), CFG)
    assert result.regime == "CONTANGO_CARRY"
    assert result.playbook == "A"


def test_contango_steep_skips_top_decile() -> None:
    result = classify(_inputs(ivts=0.85, slope_z=2.0, slope_z_top=1.28), CFG)
    assert result.regime == "CONTANGO_STEEP"
    assert result.playbook == "NONE"


def test_transition() -> None:
    result = classify(_inputs(ivts=0.96), CFG)
    assert result.regime == "TRANSITION"
    assert result.playbook == "NONE"


def test_event_tag() -> None:
    result = classify(_inputs(events_in_window=True), CFG)
    assert "EVENT" in result.tags


def test_backwardation_confirmation_signals() -> None:
    unconfirmed = classify(_inputs(ivts=1.02), CFG)
    assert unconfirmed.confirmed is False

    confirmed = classify(
        _inputs(
            ivts=1.02,
            front_iv_lower_high_2d=True,
            vix9d_below_vix=True,
            spy_up_day=True,
        ),
        CFG,
    )
    assert confirmed.confirmed is True


def test_confirmed_is_none_outside_playbook_b() -> None:
    result = classify(_inputs(ivts=0.85, slope_z=0.5, slope_z_top=1.28), CFG)
    assert result.confirmed is None
