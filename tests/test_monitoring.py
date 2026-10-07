import numpy as np
import pandas as pd
import pytest

from petlr.monitoring import (
    check_freshness,
    check_projection_vs_actual,
    check_segment_drift,
    monitor,
    psi,
)


def test_psi_identical_is_zero_and_shift_is_large():
    rng = np.random.default_rng(0)
    ref = rng.normal(50, 10, 5_000)
    assert psi(ref, ref) == pytest.approx(0, abs=1e-9)
    assert psi(ref, rng.normal(50, 10, 5_000)) < 0.02  # same distribution, new sample
    assert psi(ref, rng.normal(65, 10, 5_000)) > 0.25  # mean moved 1.5 sd


def _monthly(last="2026-09-01"):
    months = pd.date_range(end=last, periods=24, freq="MS")
    rows = []
    for region in ["West", "Midwest", "Northeast"]:
        for g in range(5):
            for i, m in enumerate(months):
                pppm = 50 * 1.08 ** (i / 12)
                if region == "West" and i >= 20:  # last 4 months: +20% shock
                    pppm *= 1.20
                rows.append({"group_id": f"{region[:2]}{g}", "region": region, "month": m,
                             "pet_months": 100, "claims_capped": pppm * 100,
                             "claims": pppm * 100, "premium": 7_000.0, "dog_months": 70,
                             "claim_count": 8, "completion_factor": 1.0})
    return pd.DataFrame(rows)


def test_freshness_levels():
    m = _monthly("2026-09-01")
    assert check_freshness(m, pd.Timestamp("2026-10-06"))[0]["status"] == "ok"
    assert check_freshness(m, pd.Timestamp("2026-11-06"))[0]["status"] == "warn"
    assert check_freshness(m, pd.Timestamp("2027-01-06"))[0]["status"] == "fail"


def test_segment_drift_finds_the_shocked_region():
    c = check_segment_drift(_monthly())
    assert c["status"] == "fail"  # +20% vs others is above the 15% fail line
    assert c["message"].startswith("West")
    assert c["value"] == pytest.approx(0.20, abs=1e-6)


def test_no_shock_no_segment_drift():
    m = _monthly()
    m["claims_capped"] = m.groupby("region")["claims_capped"].transform("first")
    assert check_segment_drift(m)["status"] == "ok"


def test_projection_vs_actual_grading():
    def bt(ae):
        return {"by_month": [{"month": "2026-04-01", "actual": ae * 100, "expected": 100.0,
                              "a_to_e": ae}]}
    assert check_projection_vs_actual(_monthly(), bt(1.03))[0]["status"] == "ok"
    assert check_projection_vs_actual(_monthly(), bt(1.15))[0]["status"] == "warn"
    assert check_projection_vs_actual(_monthly(), bt(0.70))[0]["status"] == "fail"


def test_monitor_overall_is_worst_status():
    r = monitor(_monthly(), now=pd.Timestamp("2026-10-06"))
    assert r["overall"] == "fail"  # driven by the planted West shock
    assert set(r["sections"]) == {"freshness", "drift", "projection_vs_actual"}
    assert sum(r["counts"].values()) == sum(len(v) for v in r["sections"].values())


def test_monitor_on_synthetic_pipeline(cleaned):
    r = monitor(cleaned.monthly, cleaned.claims, now=pd.Timestamp("2026-10-06"))
    assert r["sections"]["freshness"][0]["status"] == "ok"
    assert r["overall"] in {"ok", "warn", "fail"}
