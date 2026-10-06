import pandas as pd
import pytest

from petlr.backtest import history_as_of, run_backtest
from petlr.projection import Assumptions


def perfect_world(n_groups=4, n_months=18):
    """Every group costs exactly $40 per pet per month, forever, paid same day."""
    months = pd.date_range("2025-04-01", periods=n_months, freq="MS")
    monthly, claims = [], []
    for g in range(n_groups):
        gid, pets = f"G{g:04d}", 50 * (g + 1)
        for m in months:
            monthly.append({"group_id": gid, "month": m, "pet_months": pets,
                            "premium": 60.0 * pets, "segment": "s", "region": "West",
                            "deductible": 250, "species_mix": "mixed", "size_band": "medium",
                            "tenure_months": 24, "claims_reported": 0, "claim_count": 0,
                            "large_claim_count": 0, "completion_factor": 1.0,
                            "claims_capped": 40.0 * pets, "claims_excess": 0.0,
                            "claims": 40.0 * pets})
            claims.append({"group_id": gid, "service_month": m, "paid_date": m,
                           "amount": 40.0 * pets, "capped": 40.0 * pets, "excess": 0.0,
                           "is_large": False, "lag_months": 0})
    return pd.DataFrame(monthly), pd.DataFrame(claims)


def test_perfect_world_has_zero_error():
    monthly, claims = perfect_world()
    r = run_backtest(monthly, claims, holdout=6,
                     a=Assumptions(annual_trend=0.0, use_seasonality=False))
    for model in ("credibility", "own_only", "bench_only"):
        assert r["models"][model]["group"]["wape"] == pytest.approx(0, abs=1e-9)
        assert r["models"][model]["portfolio"]["bias"] == pytest.approx(0, abs=1e-9)
    assert len(r["by_month"]) == 6
    assert r["cutoff"] == "2026-03-01"


def test_no_future_leakage():
    monthly, claims = perfect_world()
    # A claim for Feb 2026 that was only paid in May 2026 (after the cutoff).
    late = {"group_id": "G0000", "service_month": pd.Timestamp("2026-02-01"),
            "paid_date": pd.Timestamp("2026-05-10"), "amount": 9_999.0,
            "capped": 5_000.0, "excess": 4_999.0, "is_large": True, "lag_months": 3}
    claims = pd.concat([claims, pd.DataFrame([late])], ignore_index=True)
    hist = history_as_of(monthly, claims, pd.Timestamp("2026-03-01"))
    feb = hist[(hist["group_id"] == "G0000") & (hist["month"] == "2026-02-01")]
    assert feb["claims_reported"].iloc[0] == pytest.approx(40.0 * 50)  # late claim not seen
    assert hist["month"].max() == pd.Timestamp("2026-03-01")


def test_backtest_on_synthetic_data(cleaned):
    r = run_backtest(cleaned.monthly, cleaned.claims, holdout=6)
    assert set(r["models"]) == {"credibility", "own_only", "bench_only"}
    for m in r["models"].values():
        assert 0 <= m["group"]["wape"] < 1
        assert m["groups_scored"] > 0
    assert len(r["by_month"]) == 6
    assert {b["band"] for b in r["by_credibility_band"]} == {
        "low (Z<0.3)", "mid (0.3-0.7)", "high (Z>=0.7)"}
    # The blend should never be worse than the worse of the two extremes.
    cred = r["models"]["credibility"]["group"]["wape"]
    worst = max(r["models"]["own_only"]["group"]["wape"],
                r["models"]["bench_only"]["group"]["wape"])
    assert cred <= worst
