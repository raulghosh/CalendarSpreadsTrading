from calscan.domain.exit_tree import ExitInputs, decide_exit
from calscan.settings import AppConfig

CFG = AppConfig.load()
TARGETS_A = CFG.targets.playbook_A
TARGETS_B = CFG.targets.playbook_B
REGIME = CFG.regime
DELTA_BAND = CFG.exit_tree.delta_band


def _inputs(**overrides: object) -> ExitInputs:
    base: dict[str, object] = dict(
        dte1=20,
        spot=6000.0,
        strike=6000.0,
        sigma_entry_pts=240.0,
        playbook="A",
        ivts=0.85,
        pnl_pct=0.0,
        net_delta=0.0,
        event_in_front_window_within_3_days=False,
    )
    base.update(overrides)
    return ExitInputs(**base)  # type: ignore[arg-type]


def test_time_stop() -> None:
    verdict = decide_exit(_inputs(dte1=10), TARGETS_A, REGIME, DELTA_BAND)
    assert verdict.action == "CLOSE"
    assert verdict.reason == "time stop"

    # boundary: dte1 == time_stop_dte triggers, one more day does not
    assert decide_exit(_inputs(dte1=11), TARGETS_A, REGIME, DELTA_BAND).reason != "time stop"


def test_distance_stop() -> None:
    # distance = |6400-6000|/240 = 1.667 sigma, past the 1.25 threshold; dte1 stays above the
    # time-stop floor so that branch doesn't preempt this one
    verdict = decide_exit(
        _inputs(dte1=20, spot=6400.0, strike=6000.0, sigma_entry_pts=240.0),
        TARGETS_A,
        REGIME,
        DELTA_BAND,
    )
    assert verdict.action == "CLOSE"
    assert verdict.reason == "distance stop"


def test_distance_stop_boundary_is_strictly_greater_than() -> None:
    # exactly at the threshold should NOT trigger (">", not ">=")
    at_boundary = _inputs(spot=6000.0 + 240.0 * 1.25, strike=6000.0, sigma_entry_pts=240.0)
    assert decide_exit(at_boundary, TARGETS_A, REGIME, DELTA_BAND).reason != "distance stop"


def test_playbook_a_curve_inverted() -> None:
    verdict = decide_exit(_inputs(playbook="A", ivts=1.05), TARGETS_A, REGIME, DELTA_BAND)
    assert verdict.action == "CLOSE"
    assert verdict.reason == "curve inverted vs carry thesis"


def test_playbook_b_curve_normalized() -> None:
    verdict = decide_exit(_inputs(playbook="B", ivts=0.85), TARGETS_B, REGIME, DELTA_BAND)
    assert verdict.action == "CLOSE"
    assert verdict.reason == "curve normalized; thesis realized"


def test_playbook_b_does_not_trigger_a_condition_and_vice_versa() -> None:
    # playbook B with high ivts should NOT trip the "A: curve inverted" branch
    verdict = decide_exit(_inputs(playbook="B", ivts=1.05), TARGETS_B, REGIME, DELTA_BAND)
    assert verdict.reason != "curve inverted vs carry thesis"
    # playbook A with low ivts should NOT trip the "B: curve normalized" branch
    verdict = decide_exit(_inputs(playbook="A", ivts=0.5), TARGETS_A, REGIME, DELTA_BAND)
    assert verdict.reason != "curve normalized; thesis realized"


def test_pnl_stop() -> None:
    verdict = decide_exit(_inputs(pnl_pct=-0.40), TARGETS_A, REGIME, DELTA_BAND)
    assert verdict.action == "CLOSE"
    assert verdict.reason == "P&L stop"


def test_target_hit() -> None:
    verdict = decide_exit(_inputs(playbook="A", pnl_pct=0.30), TARGETS_A, REGIME, DELTA_BAND)
    assert verdict.action == "CLOSE_OR_SCALE"
    assert verdict.reason == "target hit"

    # playbook B has a higher profit target (0.50) — 0.30 shouldn't trigger it there
    verdict_b = decide_exit(_inputs(playbook="B", pnl_pct=0.30), TARGETS_B, REGIME, DELTA_BAND)
    assert verdict_b.reason != "target hit"


def test_hedge_on_delta_band_breach() -> None:
    verdict = decide_exit(_inputs(net_delta=DELTA_BAND + 1), TARGETS_A, REGIME, DELTA_BAND)
    assert verdict.action == "HEDGE"
    assert verdict.reason == "delta band breached"

    verdict_negative = decide_exit(
        _inputs(net_delta=-(DELTA_BAND + 1)), TARGETS_A, REGIME, DELTA_BAND
    )
    assert verdict_negative.action == "HEDGE"  # magnitude, not sign


def test_wing_or_reduce_on_event_in_window() -> None:
    verdict = decide_exit(
        _inputs(event_in_front_window_within_3_days=True), TARGETS_A, REGIME, DELTA_BAND
    )
    assert verdict.action == "WING_OR_REDUCE"
    assert verdict.reason == "event inside front window"


def test_hold_when_nothing_triggers() -> None:
    verdict = decide_exit(_inputs(), TARGETS_A, REGIME, DELTA_BAND)
    assert verdict.action == "HOLD"
    assert verdict.reason == "no trigger"


def test_first_match_wins_time_stop_beats_everything() -> None:
    # a position that would ALSO hit distance stop, pnl stop, and delta band, but time_stop
    # fires first
    verdict = decide_exit(
        _inputs(
            dte1=5,
            spot=7000.0,
            strike=6000.0,
            sigma_entry_pts=100.0,
            pnl_pct=-0.9,
            net_delta=999.0,
        ),
        TARGETS_A,
        REGIME,
        DELTA_BAND,
    )
    assert verdict.reason == "time stop"


def test_first_match_wins_distance_stop_beats_pnl_and_delta() -> None:
    verdict = decide_exit(
        _inputs(
            dte1=20,
            spot=7000.0,
            strike=6000.0,
            sigma_entry_pts=100.0,
            pnl_pct=-0.9,
            net_delta=999.0,
        ),
        TARGETS_A,
        REGIME,
        DELTA_BAND,
    )
    assert verdict.reason == "distance stop"


def test_first_match_wins_pnl_stop_beats_hedge_and_event() -> None:
    verdict = decide_exit(
        _inputs(pnl_pct=-0.9, net_delta=999.0, event_in_front_window_within_3_days=True),
        TARGETS_A,
        REGIME,
        DELTA_BAND,
    )
    assert verdict.reason == "P&L stop"


def test_first_match_wins_hedge_beats_event() -> None:
    verdict = decide_exit(
        _inputs(net_delta=999.0, event_in_front_window_within_3_days=True),
        TARGETS_A,
        REGIME,
        DELTA_BAND,
    )
    assert verdict.action == "HEDGE"
