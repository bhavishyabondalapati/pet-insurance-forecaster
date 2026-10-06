"""Projection tests. Every expected number below is worked out by hand in the comments."""

import numpy as np
import pandas as pd
import pytest

from petlr.projection import (
    Assumptions,
    aggregate,
    blend,
    credibility_z,
    estimate_seasonality,
    estimate_trend,
    project,
    trend_factor,
)

LAST = pd.Timestamp("2026-09-01")


def make_monthly(rows):
    """rows: (group_id, months_before_last, pets, capped, excess, premium, segment)."""
    df = pd.DataFrame(rows, columns=["group_id", "back", "pet_months", "claims_capped",
                                     "claims_excess", "premium", "segment"])
    df["month"] = [LAST - pd.DateOffset(months=b) for b in df.pop("back")]
    df["claims"] = df["claims_capped"] + df["claims_excess"]
    for col, val in {"region": "West", "deductible": 250, "species_mix": "mixed",
                     "size_band": "medium", "tenure_months": 30}.items():
        df[col] = val
    return df


# --- the three formulas ------------------------------------------------------
def test_credibility_z_by_hand():
    # sqrt(1500 / 6000) = sqrt(0.25) = 0.5
    assert credibility_z(1500, 6000) == pytest.approx(0.5)
    # sqrt(5400 / 6000) = sqrt(0.9) = 0.948683
    assert credibility_z(5400, 6000) == pytest.approx(0.948683, abs=1e-6)
    # more exposure than the threshold is capped at 1
    assert credibility_z(24000, 6000) == 1.0
    assert credibility_z(0, 6000) == 0.0


def test_trend_factor_by_hand():
    assert trend_factor(0.10, 12) == pytest.approx(1.10)
    assert trend_factor(0.10, 6) == pytest.approx(1.0488088, abs=1e-7)  # sqrt(1.1)
    assert trend_factor(0.08, 24) == pytest.approx(1.1664)  # 1.08^2


def test_blend_by_hand():
    # 0.5 * 50 + 0.5 * 35 = 42.5
    assert blend(50, 35, 0.5) == pytest.approx(42.5)
    assert blend(50, 35, 1.0) == 50 and blend(50, 35, 0.0) == 35


# --- the full projection, no trend, no seasonality --------------------------
def test_projection_blend_by_hand():
    rows = []
    for back in range(12):
        rows.append(("G1", back, 100, 5_000.0, 0.0, 7_000.0, "seg"))   # own = 50
        rows.append(("G2", back, 300, 9_000.0, 0.0, 12_000.0, "seg"))  # own = 30
    a = Assumptions(annual_trend=0.0, credibility_threshold=4_800, use_seasonality=False)
    r = project(make_monthly(rows), a)
    s = r.summary.set_index("group_id")

    # pet-months: G1 = 1,200, G2 = 3,600. Segment benchmark:
    # (60,000 + 108,000) / (1,200 + 3,600) = 168,000 / 4,800 = 35
    assert s.loc["G1", "own_pppm"] == pytest.approx(50)
    assert s.loc["G2", "own_pppm"] == pytest.approx(30)
    assert s.loc["G1", "bench_pppm"] == pytest.approx(35)

    # Z1 = sqrt(1200/4800) = 0.5          -> 0.5*50 + 0.5*35 = 42.5
    # Z2 = sqrt(3600/4800) = 0.866025     -> 0.866025*30 + 0.133975*35 = 30.669873
    assert s.loc["G1", "z"] == pytest.approx(0.5)
    assert s.loc["G1", "blended_pppm"] == pytest.approx(42.5)
    assert s.loc["G2", "z"] == pytest.approx(0.866025, abs=1e-6)
    assert s.loc["G2", "blended_pppm"] == pytest.approx(30.669873, abs=1e-5)

    # G1, 12 months: 42.5 * 100 pets * 12 = 51,000 claims; premium 7,000 * 12 = 84,000
    assert s.loc["G1", "proj_claims"] == pytest.approx(51_000)
    assert s.loc["G1", "proj_premium"] == pytest.approx(84_000)
    assert s.loc["G1", "proj_lr"] == pytest.approx(51_000 / 84_000)
    assert len(r.monthly) == 2 * 12


def test_large_claim_load_by_hand():
    rows = [("G1", b, 100, 5_000.0, 0.0, 7_000.0, "seg") for b in range(12)]
    rows[0] = ("G1", 0, 100, 5_000.0, 6_000.0, 7_000.0, "seg")  # one large excess of 6,000
    a = Assumptions(annual_trend=0.0, credibility_threshold=1, use_seasonality=False)
    r = project(make_monthly(rows), a)
    # load = 6,000 excess / 60,000 capped = 0.10 -> expected PPPM = 50 * 1.10 = 55
    assert r.context["large_claim_load"] == pytest.approx(0.10)
    assert r.summary.loc[0, "expected_pppm"] == pytest.approx(55.0)


def test_trend_normalization_and_projection_by_hand():
    # Two months of history, 6 months apart, both $1,000 claims on 10 pets.
    rows = [("G1", 6, 10, 1_000.0, 0.0, 1_500.0, "seg"),
            ("G1", 0, 10, 1_000.0, 0.0, 1_500.0, "seg")]
    a = Assumptions(annual_trend=0.10, credibility_threshold=1, use_seasonality=False)
    r = project(make_monthly(rows), a)
    s = r.summary.iloc[0]
    # Older month trended up 6 months: 1,000 * 1.1^0.5 = 1,048.8088
    # own = (1,048.8088 + 1,000) / 20 pet-months = 102.44044
    assert s["own_pppm"] == pytest.approx(102.44044, abs=1e-4)
    # Month h=12: 102.44044 * 1.1 = 112.68448 PPPM * 10 pets = 1,126.8448
    m12 = r.monthly[r.monthly["h"] == 12].iloc[0]
    assert m12["claims"] == pytest.approx(1_126.8448, abs=1e-3)
    assert m12["loss_ratio"] == pytest.approx(1_126.8448 / 1_500, abs=1e-6)


def test_premium_change_and_at_risk_flag():
    rows = [("G1", b, 100, 8_000.0, 0.0, 10_000.0, "seg") for b in range(12)]  # LR 0.80
    base = Assumptions(annual_trend=0.0, credibility_threshold=1, use_seasonality=False,
                       at_risk_lr=0.75)
    r = project(make_monthly(rows), base)
    assert r.summary.loc[0, "proj_lr"] == pytest.approx(0.80)
    assert bool(r.summary.loc[0, "at_risk"])
    # +10% rate change: 0.80 / 1.10 = 0.72727 -> no longer at risk
    r2 = project(make_monthly(rows), Assumptions(**{**base.__dict__, "premium_change": 0.10}))
    assert r2.summary.loc[0, "proj_lr"] == pytest.approx(0.727273, abs=1e-6)
    assert not bool(r2.summary.loc[0, "at_risk"])


def test_cancelled_group_not_projected():
    rows = [("G1", b, 100, 5_000.0, 0.0, 7_000.0, "seg") for b in range(12)]
    rows += [("G2", b, 50, 2_000.0, 0.0, 3_000.0, "seg") for b in range(3, 12)]  # gone
    r = project(make_monthly(rows), Assumptions(annual_trend=0.0, use_seasonality=False))
    assert r.summary["group_id"].tolist() == ["G1"]
    # ... but G2's experience still counts toward the segment benchmark
    # (60,000 + 18,000) / (1,200 + 450) = 47.2727
    assert r.summary.loc[0, "bench_pppm"] == pytest.approx(47.272727, abs=1e-5)


# --- estimating seasonality and trend from data -----------------------------
def _portfolio(trend, season):
    months = pd.date_range("2024-10-01", periods=24, freq="MS")
    t = np.arange(24)
    pppm = 50 * (1 + trend) ** (t / 12) * np.array([season[m.month] for m in months])
    return pd.DataFrame({"month": months, "pet_months": 1000, "claims": pppm * 1000})


def test_estimates_recover_known_seasonality_and_trend():
    season = {m: 1 + 0.1 * np.sin(2 * np.pi * (m - 4) / 12) for m in range(1, 13)}
    df = _portfolio(0.08, season)
    # Year-over-year: every month is exactly 1.08x the same month last year.
    assert estimate_trend(df) == pytest.approx(0.08, abs=1e-9)
    s = estimate_seasonality(df)
    assert s.mean() == pytest.approx(1.0)
    assert s.idxmax() == 7 and s.idxmin() == 1
    assert s[7] == pytest.approx(season[7] / np.mean(list(season.values())), abs=0.01)


def test_short_history_means_no_seasonality():
    df = _portfolio(0.0, {m: 1.0 for m in range(1, 13)}).head(6)
    assert (estimate_seasonality(df) == 1.0).all()


def test_aggregate_credibility_on_every_number():
    rows = []
    for back in range(12):
        rows.append(("G1", back, 100, 5_000.0, 0.0, 7_000.0, "seg"))
        rows.append(("G2", back, 300, 9_000.0, 0.0, 12_000.0, "seg"))
    hist = make_monthly(rows)
    a = Assumptions(annual_trend=0.0, credibility_threshold=4_800, use_seasonality=False)
    r = project(hist, a)
    out = aggregate(hist, r, ["G1", "G2"], a.credibility_threshold)
    h = out["headline"]
    # avg_z = (0.5*1200 + 0.866025*3600) / 4800 = 0.774519
    assert h["avg_z"] == pytest.approx(0.774519, abs=1e-6)
    assert h["pooled_z"] == 1.0  # 4,800 pet-months together = threshold
    assert h["groups"] == 2 and len(out["projection"]) == 12
    assert all("avg_z" in m for m in out["projection"])
