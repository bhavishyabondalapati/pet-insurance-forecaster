"""Phase 7 - monitoring: is the data fresh, has it drifted, is the model still right?

Every check returns the same shape so the API and website can show them alike:

    {"name", "status": ok|warn|fail, "value", "threshold", "message", "details"}

Run:  python -m petlr.monitoring
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from petlr import config
from petlr.generate import last_complete_month
from petlr.pipeline import load_clean, month_diff

PSI_WARN, PSI_FAIL = 0.10, 0.25  # common rule of thumb
AE_WARN, AE_FAIL = 0.10, 0.20  # |actual / expected - 1|
SEGMENT_WARN, SEGMENT_FAIL = 0.08, 0.15  # segment growth vs portfolio growth
RANK = {"ok": 0, "warn": 1, "fail": 2}


def _check(name, status, value, threshold, message, details=None) -> dict:
    return {"name": name, "status": status, "value": value, "threshold": threshold,
            "message": message, "details": details or {}}


def _grade(value: float, warn: float, fail: float) -> str:
    return "fail" if value >= fail else "warn" if value >= warn else "ok"


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------
def check_freshness(monthly: pd.DataFrame, now: pd.Timestamp | None = None,
                    last_run_epoch: float | None = None, max_age_hours: float = 36) -> list[dict]:
    now = pd.Timestamp(now or pd.Timestamp.now())
    latest = monthly["month"].max()
    expected = last_complete_month(now)
    lag = int(month_diff(pd.Series([expected]), pd.Series([latest])).iloc[0])
    status = "ok" if lag <= 0 else "warn" if lag == 1 else "fail"
    checks = [_check(
        "data_freshness", status, lag, "0 months behind",
        f"Latest data month {latest:%b %Y}; expected {expected:%b %Y} "
        f"({'up to date' if lag <= 0 else f'{lag} month(s) behind'}).",
        {"latest_month": latest.date().isoformat(), "expected_month": expected.date().isoformat()},
    )]
    if last_run_epoch is not None:
        age_h = (time.time() - last_run_epoch) / 3600
        checks.append(_check(
            "pipeline_last_run", "ok" if age_h <= max_age_hours else "warn", round(age_h, 1),
            f"<= {max_age_hours} hours", f"Pipeline last wrote clean data {age_h:.1f} hours ago."))
    return checks


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------
def psi(reference: np.ndarray, current: np.ndarray, bins: int = 10) -> float:
    """Population Stability Index.

    Bin both samples using the reference's deciles, then sum
    (cur% - ref%) * ln(cur% / ref%). 0 = identical; > 0.25 = big shift.
    """
    reference, current = np.asarray(reference, float), np.asarray(current, float)
    reference, current = reference[np.isfinite(reference)], current[np.isfinite(current)]
    if len(reference) < bins or len(current) < bins:
        return 0.0
    edges = np.unique(np.quantile(reference, np.linspace(0, 1, bins + 1)))
    edges[0], edges[-1] = -np.inf, np.inf
    ref_pct = np.histogram(reference, edges)[0] / len(reference)
    cur_pct = np.histogram(current, edges)[0] / len(current)
    ref_pct, cur_pct = np.clip(ref_pct, 1e-4, None), np.clip(cur_pct, 1e-4, None)
    return float(np.sum((cur_pct - ref_pct) * np.log(cur_pct / ref_pct)))


def _per_group(df: pd.DataFrame) -> pd.DataFrame:
    # Recent months are missing late claims, so gross counts up like dollars.
    df = df.assign(claims_dev=df["claim_count"] / df["completion_factor"])
    g = df.groupby("group_id")[["claims_capped", "pet_months", "premium", "dog_months",
                                 "claims_dev"]].sum()
    return pd.DataFrame({
        "capped_pppm": g["claims_capped"] / g["pet_months"],
        "premium_per_pet": g["premium"] / g["pet_months"],
        "dog_share": g["dog_months"] / g["pet_months"],
        "claim_frequency": g["claims_dev"] / g["pet_months"],
    })


def check_drift(monthly: pd.DataFrame, claims: pd.DataFrame | None = None,
                recent_months: int = 6) -> list[dict]:
    """Compare the most recent months with the SAME months a year earlier.

    Same calendar months on both sides, so summer-vs-winter differences don't
    show up as drift. What remains is trend plus genuine change.
    """
    last = monthly["month"].max()
    cur_start = last - pd.DateOffset(months=recent_months)
    year = pd.DateOffset(months=12)
    cur = monthly[monthly["month"] > cur_start]
    ref = monthly[(monthly["month"] > cur_start - year) & (monthly["month"] <= last - year)]
    ref_g, cur_g = _per_group(ref), _per_group(cur)

    checks = []
    for feature, label in [("capped_pppm", "group cost per pet (capped)"),
                           ("claim_frequency", "group claim frequency"),
                           ("premium_per_pet", "group premium per pet"),
                           ("dog_share", "group dog share")]:
        value = psi(ref_g[feature], cur_g[feature])
        checks.append(_check(
            f"drift_{feature}", _grade(value, PSI_WARN, PSI_FAIL), round(value, 4),
            f"PSI < {PSI_WARN}", f"PSI of {label}: {value:.3f} (last {recent_months} months "
            "vs the same months a year earlier).", {"reference_mean": float(ref_g[feature].mean()),
                                   "current_mean": float(cur_g[feature].mean())}))

    if claims is not None and len(claims):
        sm = claims["service_month"]
        ref_c = claims[(sm > cur_start - year) & (sm <= last - year)]["capped"]
        cur_c = claims[sm > cur_start]["capped"]
        value = psi(ref_c.to_numpy(), cur_c.to_numpy())
        checks.append(_check(
            "drift_claim_severity", _grade(value, PSI_WARN, PSI_FAIL), round(value, 4),
            f"PSI < {PSI_WARN}", f"PSI of individual claim size: {value:.3f} "
            "(some drift is expected from the vet-cost trend).",
            {"reference_mean": float(ref_c.mean()), "current_mean": float(cur_c.mean())}))
    checks.append(check_segment_drift(monthly))
    return checks


def check_segment_drift(monthly: pd.DataFrame, months: int = 4, by: str = "region") -> dict:
    """Is any segment's cost growing much faster than everyone else's?

    Compares the last `months` months with the same months a year earlier, so
    seasonality cancels out. A segment whose growth beats the other segments' by
    more than the threshold is flagged - an early warning the model will
    under-project it. Uses capped claims so one huge claim can't trigger it.
    """
    last = monthly["month"].max()
    recent = monthly[monthly["month"] > last - pd.DateOffset(months=months)]
    year_ago = monthly[(monthly["month"] > last - pd.DateOffset(months=12 + months))
                       & (monthly["month"] <= last - pd.DateOffset(months=12))]

    def totals(df):
        return df.groupby(by)[["claims_capped", "pet_months"]].sum()

    rec, ago = totals(recent), totals(year_ago)
    growth = ((rec["claims_capped"] / rec["pet_months"])
              / (ago["claims_capped"] / ago["pet_months"])).dropna()
    # Compare each segment with everyone else (the segment itself excluded).
    rest_rec, rest_ago = rec.sum() - rec, ago.sum() - ago
    rest_growth = ((rest_rec["claims_capped"] / rest_rec["pet_months"])
                   / (rest_ago["claims_capped"] / rest_ago["pet_months"]))
    excess = (growth / rest_growth - 1).dropna()
    worst = excess.idxmax() if len(excess) else None
    value = float(excess.max()) if len(excess) else 0.0
    return _check(
        f"segment_drift_{by}", _grade(value, SEGMENT_WARN, SEGMENT_FAIL), round(value, 4),
        f"< +{SEGMENT_WARN:.0%} vs other segments",
        (f"{worst} capped costs grew {growth[worst] - 1:+.1%} year-over-year vs "
         f"{rest_growth[worst] - 1:+.1%} for all other {by}s (last {months} months)."
         if worst else "Not enough history."),
        {"segment_growth": {k: round(float(v - 1), 4) for k, v in growth.items()},
         "excess_vs_rest": {k: round(float(v), 4) for k, v in excess.items()}},
    )


# ---------------------------------------------------------------------------
# Projection vs actual
# ---------------------------------------------------------------------------
def check_projection_vs_actual(monthly: pd.DataFrame, backtest: dict | None = None,
                               snapshots_dir: Path | None = None) -> list[dict]:
    checks = []
    if backtest:
        rows = pd.DataFrame(backtest["by_month"])
        total_ae = rows["actual"].sum() / rows["expected"].sum()
        worst = rows.loc[(rows["a_to_e"] - 1).abs().idxmax()]
        dev = abs(total_ae - 1)
        checks.append(_check(
            "backtest_actual_vs_expected", _grade(dev, AE_WARN, AE_FAIL), round(total_ae, 4),
            f"within ±{AE_WARN:.0%}",
            f"Over the {len(rows)} hidden months, actual claims were {total_ae:.2f}x the "
            f"projection (worst month {worst['month']}: {worst['a_to_e']:.2f}x).",
            {"by_month": rows[["month", "a_to_e"]].to_dict("records")}))

    # Saved projections whose months have now happened.
    if snapshots_dir and snapshots_dir.exists():
        for path in sorted(snapshots_dir.glob("projection_*.parquet")):
            snap = pd.read_parquet(path)
            m = monthly.merge(snap[["group_id", "month", "pppm"]], on=["group_id", "month"])
            if m.empty:
                continue
            ae = m["claims"].sum() / (m["pppm"] * m["pet_months"]).sum()
            dev = abs(ae - 1)
            checks.append(_check(
                f"snapshot_{path.stem.removeprefix('projection_')}",
                _grade(dev, AE_WARN, AE_FAIL), round(float(ae), 4), f"within ±{AE_WARN:.0%}",
                f"Projection saved {path.stem.removeprefix('projection_')}: actual claims are "
                f"{ae:.2f}x expected over {m['month'].nunique()} month(s) so far."))
    if not checks:
        checks.append(_check("projection_vs_actual", "ok", None, None,
                             "No projected months have actuals yet."))
    return checks


# ---------------------------------------------------------------------------
# Everything together
# ---------------------------------------------------------------------------
def monitor(monthly: pd.DataFrame, claims: pd.DataFrame | None = None,
            backtest: dict | None = None, snapshots_dir: Path | None = None,
            now: pd.Timestamp | None = None, last_run_epoch: float | None = None) -> dict:
    sections = {
        "freshness": check_freshness(monthly, now, last_run_epoch),
        "drift": check_drift(monthly, claims),
        "projection_vs_actual": check_projection_vs_actual(monthly, backtest, snapshots_dir),
    }
    all_checks = [c for cs in sections.values() for c in cs]
    overall = max((c["status"] for c in all_checks), key=RANK.__getitem__)
    return {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "overall": overall,
        "counts": {s: sum(c["status"] == s for c in all_checks) for s in RANK},
        "sections": sections,
    }


def run(data_dir: Path = config.DATA_DIR, now: pd.Timestamp | None = None) -> dict:
    clean_dir, out_dir = data_dir / "clean", data_dir / "out"
    clean = load_clean(clean_dir)
    bt_path = out_dir / "backtest.json"
    backtest = json.loads(bt_path.read_text()) if bt_path.exists() else None
    result = monitor(clean["monthly"], clean["claims"], backtest, out_dir / "projections", now,
                     (clean_dir / "meta.json").stat().st_mtime)
    (out_dir / "monitoring.json").write_text(json.dumps(result, indent=2, default=str))
    return result


def main() -> None:
    r = run()
    print(f"Monitoring: {r['overall'].upper()}  {r['counts']}")
    for section, checks in r["sections"].items():
        print(f"\n{section}")
        for c in checks:
            print(f"  [{c['status']:4}] {c['name']}: {c['message']}")


if __name__ == "__main__":
    main()
