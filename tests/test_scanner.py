import json
from datetime import date
from pathlib import Path

from alpaca.data.models.snapshots import OptionsSnapshot

from calscan.adapters import alpaca, schwab
from calscan.domain.models import Chain, Expiry
from calscan.domain.scanner import (
    Candidate,
    Gates,
    build_candidates,
    enumerate_expiry_pairs,
    rank_candidates,
)
from calscan.settings import AppConfig

FIXTURES = Path(__file__).parent / "fixtures"
CFG = AppConfig.load()


def _load_spx_chain() -> Chain:
    raw = json.loads((FIXTURES / "schwab_spx_chain.json").read_text())
    return schwab.parse_chain(raw, product="SPX")


def _load_xsp_chain() -> Chain:
    raw = json.loads((FIXTURES / "schwab_xsp_chain.json").read_text())
    return schwab.parse_chain(raw, product="XSP")


def _load_spy_chain() -> Chain:
    raw = json.loads((FIXTURES / "alpaca_spy_chain.json").read_text())
    snapshots = {s: OptionsSnapshot(s, d) for s, d in raw["snapshots"].items()}
    return alpaca.parse_chain(
        snapshots, product="SPY", underlying_price=raw["underlying_price"], as_of=date(2026, 9, 13)
    )


def test_enumerate_expiry_pairs_respects_dte_and_ratio_window() -> None:
    expiries = [
        Expiry(date=date(2026, 10, 9), dte=30, am_settled=False),
        Expiry(date=date(2026, 11, 13), dte=65, am_settled=False),  # ratio 2.17 -> in range
        Expiry(date=date(2026, 10, 2), dte=23, am_settled=False),
        Expiry(date=date(2026, 10, 23), dte=44, am_settled=False),  # ratio 44/23=1.91 -> in range
        Expiry(date=date(2026, 9, 25), dte=16, am_settled=False),  # front too short, excluded
    ]
    pairs = enumerate_expiry_pairs(expiries, CFG.expiry_selection)
    fronts = {p[0].dte for p in pairs}
    assert 16 not in fronts
    assert any(p[0].dte == 30 and p[1].dte == 65 for p in pairs)


def test_enumerate_expiry_pairs_drops_am_settled_fronts_when_configured() -> None:
    expiries = [
        Expiry(date=date(2026, 9, 18), dte=30, am_settled=True),
        Expiry(date=date(2026, 11, 20), dte=93, am_settled=False),
    ]
    pairs = enumerate_expiry_pairs(expiries, CFG.expiry_selection)
    assert pairs == []


def _build_for(product: str, chain: Chain, rv10: float = 0.10) -> list[Candidate]:
    return build_candidates(
        product=product,
        contracts=chain.contracts,
        expiries=chain.expiries,
        spot=chain.underlying_price,
        multiplier=CFG.products[product].multiplier,
        style=CFG.products[product].style,
        r=CFG.rates.risk_free,
        q=CFG.rates.dividend_yield,
        expiry_cfg=CFG.expiry_selection,
        strikes_cfg=CFG.strikes,
        gates_cfg=CFG.gates,
        wvega_alphas=[CFG.vol_shock_alpha.baseline, *CFG.vol_shock_alpha.stress_cases],
        stress_alpha=max(CFG.vol_shock_alpha.stress_cases),
        rv10=rv10,
        nav=CFG.nav,
        slope_z_favourable=True,
        as_of=date(2026, 9, 13),
    )


def test_scanner_produces_candidates_for_all_three_products() -> None:
    for product, chain in [
        ("SPX", _load_spx_chain()),
        ("XSP", _load_xsp_chain()),
        ("SPY", _load_spy_chain()),
    ]:
        candidates = _build_for(product, chain)
        assert len(candidates) >= 1, f"{product} produced no candidates"
        assert all(c.product == product for c in candidates)


def test_xsp_produces_tradeable_candidates() -> None:
    # XSP (1/10th SPX notional) fits the $100k-NAV debit gate; real OI + european settlement
    # clear the rest — should be fully tradeable.
    candidates = _build_for("XSP", _load_xsp_chain())
    assert any(c.tradeable for c in candidates), "XSP had no tradeable candidate"


def test_spx_candidates_fail_debit_gate_at_100k_nav() -> None:
    # SPX's 100x multiplier on a $6000 underlying prices ATM calendars around $5-7k/contract —
    # correctly gated out at the default 1.5%-of-$100k-NAV debit cap. This is why XSP/SPY exist.
    candidates = _build_for("SPX", _load_spx_chain())
    assert candidates
    assert all(not c.gates.debit_ok for c in candidates)
    assert all(not c.tradeable for c in candidates)


def test_spy_candidates_fail_oi_gate_since_alpaca_has_no_open_interest() -> None:
    chain = _load_spy_chain()
    candidates = _build_for("SPY", chain)
    assert candidates  # still produces candidates for ranking/display
    assert all(not c.gates.oi_ok for c in candidates)
    assert all(not c.tradeable for c in candidates)


def test_rank_candidates_puts_tradeable_first_and_sorts_by_soft_score() -> None:
    candidates = _build_for("XSP", _load_xsp_chain())
    ranked = rank_candidates(candidates, playbook="A", slope_z=0.5)
    tradeable_flags = [c.tradeable for c in ranked]
    # once we hit the first non-tradeable, there should be no tradeable ones after it
    if False in tradeable_flags:
        first_false = tradeable_flags.index(False)
        assert all(not f for f in tradeable_flags[first_false:])
    scores = [c.soft_score for c in ranked if c.tradeable]
    assert scores == sorted(scores, reverse=True)


def test_gates_all_pass_property() -> None:
    passing = Gates(True, True, True, True, True, True)
    assert passing.all_pass
    failing = Gates(True, False, True, True, True, True)
    assert not failing.all_pass
