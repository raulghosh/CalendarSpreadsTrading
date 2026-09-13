from __future__ import annotations

import streamlit as st

from calscan.domain.sigma import (
    d_from_delta,
    daily_breakeven_pts,
    delta_from_d,
    expected_move_convention_check,
    sigma_pts,
    skew_delta_adjust,
    strike_from_delta,
)

st.set_page_config(page_title="Sigma Calculator — Calendar Scanner", page_icon="🧮", layout="wide")

st.title("Sigma Calculator")
st.caption("Standalone — works with no API connection.")

SIGMA_MULTIPLES = [0.25, 0.5, 0.67, 1.0, 1.28]

col1, col2, col3 = st.columns(3)
with col1:
    spot = st.number_input("Spot", min_value=0.01, value=6000.0, step=1.0)
with col2:
    iv_pct = st.number_input("IV (%)", min_value=0.01, value=14.0, step=0.1)
with col3:
    dte = st.number_input("DTE", min_value=1, value=30, step=1)

iv = iv_pct / 100
use_platform_em = st.checkbox("I have the platform's displayed expected move")
platform_em = (
    st.number_input("Platform expected move (points)", min_value=0.0, value=0.0, step=1.0)
    if use_platform_em
    else None
)

sigma1 = sigma_pts(spot, iv, dte)
st.metric("1σ (points)", f"{sigma1:,.2f}")
st.metric("Front-month daily breakeven (points)", f"{daily_breakeven_pts(spot, iv):,.2f}")

if platform_em is not None and platform_em > 0:
    convention = expected_move_convention_check(platform_em, spot, iv, dte)
    st.write(f"Expected-move convention: **{convention}**")

st.subheader("Strikes by σ-distance")
rows = []
for mult in SIGMA_MULTIPLES:
    call_flat = delta_from_d(mult)
    put_flat = call_flat - 1
    rows.append(
        {
            "σ": mult,
            "call strike": round(spot + mult * sigma1, 2),
            "call Δ (flat-vol)": round(call_flat, 4),
            "call Δ (skew-adj)": round(call_flat + skew_delta_adjust("call"), 4),
            "put strike": round(spot - mult * sigma1, 2),
            "put Δ (flat-vol)": round(put_flat, 4),
            "put Δ (skew-adj)": round(put_flat + skew_delta_adjust("put"), 4),
        }
    )
st.dataframe(rows, use_container_width=True, hide_index=True)

st.subheader("Reverse: delta → strike")
rev_col1, rev_col2 = st.columns(2)
with rev_col1:
    target_delta = st.number_input(
        "Target delta (magnitude, e.g. 0.25)", min_value=0.001, max_value=0.999, value=0.25
    )
with rev_col2:
    side = st.selectbox("Side", ["call", "put"])

d = d_from_delta(target_delta)
strike = strike_from_delta(spot, iv, dte, target_delta, side)  # type: ignore[arg-type]
st.write(f"d = **{d:.4f}**, strike = **{strike:,.2f}**")
