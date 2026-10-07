import json

import pandas as pd
import pytest

from petlr import pipeline
from petlr.pipeline import clean, completion_factors, snap_to_month
from petlr.quality import DataQualityError

AS_OF = pd.Timestamp("2026-09-30")


def _issue(report, check):
    matches = [i for i in report.raw_issues if i.check == check]
    assert matches, f"gate did not report {check}"
    return matches[0]


def test_gate_passes_and_reports_every_planted_problem(cleaned, planted):
    r = cleaned.report
    assert r.passed
    assert _issue(r, "claim_id:field_uniqueness").rows == planted["duplicate_claim_rows"]
    assert _issue(r, "billing_month:first_of_month").rows == planted["premium_dates_shifted"]
    assert _issue(r, "enroll_date:first_of_month").rows == planted["pet_enroll_dates_shifted"]
    assert (_issue(r, "cancelled_group_pet_has_term_date").rows
            == planted["cancelled_group_pets_still_listed"])
    assert _issue(r, "paid_within_60_days").rows == planted["late_paid_claims"]
    bad_start = [i for i in r.raw_issues if i.table == "groups"]
    assert sum(i.rows for i in bad_start) >= len(planted["bad_start_dates"])


def test_problems_are_actually_fixed(cleaned, planted):
    m = cleaned.monthly
    assert not cleaned.claims["claim_id"].duplicated().any()
    assert not m.duplicated(["group_id", "month"]).any()
    assert (m["month"].dt.day == 1).all()
    assert (m["pets_billed"] == m["pet_months"]).all()  # no ghost pets after cancel
    fixed = cleaned.groups.set_index("group_id").loc[planted["bad_start_dates"], "start_date"]
    assert (fixed >= pd.Timestamp("2000-01-01")).all() and (fixed <= AS_OF).all()
    # cancelled groups have no exposure on/after their cancel date
    cancelled = m[m["cancel_date"].notna()]
    assert (cancelled["month"] < cancelled["cancel_date"]).all()


def test_recent_months_are_grossed_up(cleaned):
    latest = cleaned.monthly[cleaned.monthly["month"] == "2026-09-01"]
    assert (latest["completion_factor"] < 1).all()
    assert latest["claims"].sum() > latest["claims_reported"].sum()


def test_unknown_problem_fails_loudly(raw):
    broken = {k: v.copy() for k, v in raw.items()}
    broken["premiums"].loc[0, "premium"] = -100.0  # never planted -> unknown
    with pytest.raises(DataQualityError) as exc:
        clean(broken, as_of=AS_OF)
    report = exc.value.report
    assert not report.passed
    assert any(i.severity == "fatal" and "premium" in i.check for i in report.raw_issues)
    assert "FAILED" in str(exc.value)


def test_reconciliation_failure_is_caught(raw):
    broken = {k: v.copy() for k, v in raw.items()}
    broken["premiums"].loc[5, "pets_billed"] += 3  # bill for pets that don't exist
    with pytest.raises(DataQualityError) as exc:
        clean(broken, as_of=AS_OF)
    checks = [i.check for i in exc.value.report.clean_failures]
    assert "billed_pets_match_enrolled_pets" in checks


def test_too_many_duplicates_is_fatal(raw):
    broken = {k: v.copy() for k, v in raw.items()}
    broken["claims"] = pd.concat([broken["claims"]] * 2)  # whole file sent twice
    with pytest.raises(DataQualityError) as exc:
        clean(broken, as_of=AS_OF)
    dup = next(i for i in exc.value.report.raw_issues if i.check == "claim_id:field_uniqueness")
    assert dup.severity == "fatal" and "tolerance" in dup.action


def test_report_written_even_when_gate_fails(raw, tmp_path, monkeypatch):
    broken = {k: v.copy() for k, v in raw.items()}
    broken["premiums"].loc[0, "premium"] = -1.0
    monkeypatch.setattr(pipeline, "load_raw", lambda _dir: broken)
    report_path = tmp_path / "report.json"
    with pytest.raises(DataQualityError):
        pipeline.run(tmp_path, tmp_path / "clean", report_path)
    assert json.loads(report_path.read_text())["passed"] is False


def test_snap_to_month():
    s = pd.Series(pd.to_datetime(["2025-02-28", "2025-03-01", "2025-03-31", None]))
    out = snap_to_month(s)
    assert out.iloc[0] == pd.Timestamp("2025-03-01")
    assert out.iloc[1] == pd.Timestamp("2025-03-01")
    assert out.iloc[2] == pd.Timestamp("2025-04-01")
    assert pd.isna(out.iloc[3])


def test_completion_factors_by_hand():
    # Three mature claims: $100 paid same month, $300 one month later,
    # $600 two months later -> cumulative 10%, 40%, 100%.
    claims = pd.DataFrame({
        "service_month": pd.to_datetime(["2025-01-01"] * 3),
        "lag_months": [0, 1, 2],
        "amount": [100.0, 300.0, 600.0],
    })
    cf = completion_factors(claims, pd.Timestamp("2026-01-01")).set_index("lag")["factor"]
    assert cf[0] == pytest.approx(0.10)
    assert cf[1] == pytest.approx(0.40)
    assert cf[2] == pytest.approx(1.00)
    assert cf[8] == pytest.approx(1.00)
