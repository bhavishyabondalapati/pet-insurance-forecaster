import pandas as pd

from petlr.generate import generate, last_complete_month, true_seasonality


def test_shapes_and_ranges(raw):
    groups, premiums = raw["groups"], raw["premiums"]
    assert len(groups) == 80
    months = premiums["billing_month"].dt.to_period("M").unique()
    assert len(months) == 24
    # Groups start with 10-2,000 pets (churn can nudge them slightly after).
    first = premiums.sort_values("billing_month").groupby("group_id")["pets_billed"].first()
    assert first.min() >= 8 and first.max() <= 2100


def test_every_problem_type_is_planted(planted):
    for key in ["duplicate_claim_rows", "duplicate_premium_rows",
                "cancelled_group_pets_still_listed", "late_paid_claims",
                "premium_dates_shifted", "pet_enroll_dates_shifted"]:
        assert planted[key] > 0, key
    assert len(planted["bad_start_dates"]) == 7


def test_same_seed_same_data():
    a, _ = generate(n_groups=10, end_month="2026-09", seed=1)
    b, _ = generate(n_groups=10, end_month="2026-09", seed=1)
    pd.testing.assert_frame_equal(a["claims"], b["claims"])


def test_seasonality_peaks_in_summer():
    s = true_seasonality(range(1, 13))
    assert s.argmax() + 1 == 7 and s.argmin() + 1 == 1
    assert abs(s.mean() - 1) < 1e-9


def test_last_complete_month():
    assert last_complete_month(pd.Timestamp("2026-10-06")) == pd.Timestamp("2026-09-01")
    assert last_complete_month(pd.Timestamp("2026-01-31")) == pd.Timestamp("2025-12-01")
