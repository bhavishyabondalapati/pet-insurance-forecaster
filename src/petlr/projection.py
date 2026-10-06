"""Phase 3 - credibility-weighted baseline projection.

For every active group:

    own      = group's own claims per pet per month (PPPM), last 12 months,
               de-seasonalized and trended to today's cost level
    bench    = the same number for the group's whole segment
               (species mix x deductible)
    Z        = min(1, sqrt(pet_months / threshold))       credibility
    blended  = Z * own + (1 - Z) * bench
    expected = blended * (1 + large-claim load)
    PPPM(h)  = expected * (1 + trend)^(h/12) * seasonality[calendar month]
    claims   = PPPM(h) * pets,  premium = premium per pet * (1 + rate change) * pets

Large claims are capped (config.LARGE_CLAIM_CAP) in `own` and `bench` and
added back as a portfolio-wide load, so one $20k surgery doesn't make a small
group look terrible for a year.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from petlr.pipeline import month_diff


@dataclass(frozen=True)
class Assumptions:
    """Everything a user can change. The API exposes these as query params."""

    annual_trend: float | None = None  # None -> estimate from data
    credibility_threshold: float = 2_000.0  # pet-months for Z = 1 (tuned by back-test)
    premium_change: float = 0.0  # rate change applied to projected premium
    at_risk_lr: float = 0.85  # projected LR above this -> flagged
    horizon: int = 12  # months to project
    experience_months: int = 12  # history window used for "own" experience
    use_seasonality: bool = True


@dataclass
class ProjectionResult:
    summary: pd.DataFrame  # one row per group
    monthly: pd.DataFrame  # one row per group per projected month
    context: dict = field(default_factory=dict)  # trend, seasonality, load, ...


# ---------------------------------------------------------------------------
# Small pure functions (easy to unit test by hand)
# ---------------------------------------------------------------------------
def credibility_z(pet_months, threshold: float):
    """Z = min(1, sqrt(pet_months / threshold)). Works on numbers or arrays."""
    if threshold <= 0:
        return np.ones_like(np.asarray(pet_months, dtype=float))
    return np.minimum(1.0, np.sqrt(np.asarray(pet_months, dtype=float) / threshold))


def trend_factor(annual_trend: float, months) -> np.ndarray:
    """Cost growth over `months` months at an annual rate: (1 + r)^(months/12)."""
    return (1.0 + annual_trend) ** (np.asarray(months, dtype=float) / 12.0)


def blend(own, bench, z):
    """Credibility blend: Z * own + (1 - Z) * benchmark."""
    return z * np.asarray(own) + (1 - z) * np.asarray(bench)


def _loglinear_slope(y: np.ndarray) -> tuple[float, np.ndarray]:
    """Fit log(y) = a + b*t; return (b, fitted y)."""
    t = np.arange(len(y), dtype=float)
    b, a = np.polyfit(t, np.log(y), 1)
    return b, np.exp(a + b * t)


def portfolio_pppm(monthly: pd.DataFrame) -> pd.Series:
    by_month = monthly.groupby("month")[["claims", "pet_months"]].sum().sort_index()
    return by_month["claims"] / by_month["pet_months"]


def estimate_trend(monthly: pd.DataFrame) -> float:
    """Annual cost trend from the portfolio's PPPM.

    With 15+ months: compare each month with the same month a year earlier
    (year-over-year) and take the geometric mean. Seasonality cancels out,
    because July is compared with July. Fewer months: log-linear fit.
    """
    pppm = portfolio_pppm(monthly)
    if len(pppm) >= 15:
        yoy = pppm.to_numpy()[12:] / pppm.to_numpy()[:-12]
        return float(np.exp(np.log(yoy).mean()) - 1)  # geometric mean
    if len(pppm) >= 6:
        slope, _ = _loglinear_slope(pppm.to_numpy())
        return float(np.exp(12 * slope) - 1)
    return 0.0


def estimate_seasonality(monthly: pd.DataFrame, trend: float | None = None) -> pd.Series:
    """Index by calendar month (1..12, mean 1) from the portfolio's PPPM.

    Remove the trend, then average actual / average for each calendar month.
    Needs 12+ months of data, otherwise returns all ones.
    """
    pppm = portfolio_pppm(monthly)
    ones = pd.Series(1.0, index=range(1, 13))
    if len(pppm) < 12:
        return ones
    trend = estimate_trend(monthly) if trend is None else trend
    detrended = pppm.to_numpy() / trend_factor(trend, np.arange(len(pppm)))
    ratio = pd.Series(detrended / detrended.mean(), index=pppm.index.month)
    idx = ratio.groupby(level=0).mean().reindex(range(1, 13)).fillna(1.0)
    return idx / idx.mean()


# ---------------------------------------------------------------------------
# The projection
# ---------------------------------------------------------------------------
def project(monthly: pd.DataFrame, a: Assumptions = Assumptions(),
            seasonality: pd.Series | None = None) -> ProjectionResult:
    """Project every group active in the latest month `a.horizon` months forward."""
    monthly = monthly.copy()
    last = monthly["month"].max()

    estimated_trend = estimate_trend(monthly)
    trend = estimated_trend if a.annual_trend is None else a.annual_trend
    if not a.use_seasonality:
        seasonality = pd.Series(1.0, index=range(1, 13))
    elif seasonality is None:
        seasonality = estimate_seasonality(monthly, estimated_trend)

    # 1. Normalize experience to the latest month's cost level, no seasonality.
    exp = monthly[monthly["month"] > last - pd.DateOffset(months=a.experience_months)].copy()
    age = month_diff(pd.Series(last, index=exp.index), exp["month"])
    weight = trend_factor(trend, age) / seasonality.reindex(exp["month"].dt.month).to_numpy()
    exp["adj_capped"] = exp["claims_capped"] * weight
    exp["adj_excess"] = exp["claims_excess"] * weight

    g = exp.groupby("group_id").agg(
        pet_months_exp=("pet_months", "sum"), adj_capped=("adj_capped", "sum"),
        adj_excess=("adj_excess", "sum"), segment=("segment", "last"))
    g["own_pppm"] = g["adj_capped"] / g["pet_months_exp"]

    # 2. Segment benchmark and large-claim load (portfolio wide).
    seg = g.groupby("segment")[["adj_capped", "pet_months_exp"]].sum()
    bench = (seg["adj_capped"] / seg["pet_months_exp"]).rename("bench_pppm")
    g = g.join(bench, on="segment")
    large_load = float(g["adj_excess"].sum() / g["adj_capped"].sum()) if len(g) else 0.0

    # 3. Credibility blend.
    g["z"] = credibility_z(g["pet_months_exp"], a.credibility_threshold)
    g["blended_pppm"] = blend(g["own_pppm"], g["bench_pppm"], g["z"])
    g["expected_pppm"] = g["blended_pppm"] * (1 + large_load)

    # 4. Only groups still active in the latest month get projected.
    latest = monthly[monthly["month"] == last].set_index("group_id")
    active = g.index.intersection(latest.index)
    g = g.loc[active]
    attrs = latest.loc[active, ["region", "deductible", "species_mix", "size_band",
                                "tenure_months", "pet_months", "premium"]]
    g = g.join(attrs.rename(columns={"pet_months": "pets", "premium": "premium_last"}))
    g["premium_per_pet"] = g["premium_last"] / g["pets"]

    # 5. Roll forward month by month.
    future = pd.date_range(last + pd.DateOffset(months=1), periods=a.horizon, freq="MS")
    h = np.arange(1, a.horizon + 1)
    season_f = seasonality.reindex(future.month).to_numpy()
    path = pd.DataFrame({"month": future, "h": h,
                         "factor": trend_factor(trend, h) * season_f})
    proj = g.reset_index()[["group_id", "expected_pppm", "pets", "premium_per_pet", "z"]].merge(
        path, how="cross")
    proj["pppm"] = proj["expected_pppm"] * proj["factor"]
    proj["claims"] = proj["pppm"] * proj["pets"]
    proj["premium"] = proj["premium_per_pet"] * (1 + a.premium_change) * proj["pets"]
    proj["loss_ratio"] = proj["claims"] / proj["premium"]
    proj = proj[["group_id", "month", "h", "pets", "pppm", "claims", "premium",
                 "loss_ratio", "z"]]

    # 6. Group summary with history for comparison.
    totals = proj.groupby("group_id")[["claims", "premium"]].sum()
    g["proj_claims"] = totals["claims"]
    g["proj_premium"] = totals["premium"]
    g["proj_lr"] = g["proj_claims"] / g["proj_premium"]
    hist = exp.groupby("group_id")[["claims", "premium"]].sum()
    g["hist_lr"] = hist["claims"] / hist["premium"]
    g["at_risk"] = g["proj_lr"] > a.at_risk_lr
    summary = g.reset_index().rename(columns={"index": "group_id"})
    summary = summary.drop(columns=["adj_capped", "adj_excess"])

    context = {
        "as_of_month": last.date().isoformat(),
        "annual_trend": trend,
        "estimated_trend": estimated_trend,
        "trend_source": "estimated" if a.annual_trend is None else "assumption",
        "seasonality": {int(k): round(float(v), 4) for k, v in seasonality.items()},
        "large_claim_load": large_load,
        "assumptions": asdict(a),
    }
    return ProjectionResult(summary, proj, context)


# ---------------------------------------------------------------------------
# Rolling groups up (used for portfolio and segment views)
# ---------------------------------------------------------------------------
def aggregate(history: pd.DataFrame, result: ProjectionResult, group_ids,
              threshold: float) -> dict:
    """History + projection for a set of groups, with credibility on every number.

    avg_z   = exposure-weighted average of the groups' own Z
    own_share = share of projected claims driven by the groups' own experience
    pooled_z  = Z if the whole set were treated as one big group
    """
    ids = pd.Index(group_ids)
    hist = history[history["group_id"].isin(ids)]
    s = result.summary[result.summary["group_id"].isin(ids)]
    p = result.monthly[result.monthly["group_id"].isin(ids)]

    h = hist.groupby("month")[["premium", "claims", "pet_months"]].sum().reset_index()
    h["loss_ratio"] = h["claims"] / h["premium"]
    h["pppm"] = h["claims"] / h["pet_months"]

    pm = p.assign(zc=p["z"] * p["claims"], zp=p["z"] * p["pets"])
    pm = pm.groupby("month")[["premium", "claims", "pets", "zc", "zp"]].sum().reset_index()
    pm["loss_ratio"] = pm["claims"] / pm["premium"]
    pm["pppm"] = pm["claims"] / pm["pets"]
    pm["avg_z"] = pm["zp"] / pm["pets"]

    pet_months = s["pet_months_exp"].sum()
    proj_claims, proj_premium = s["proj_claims"].sum(), s["proj_premium"].sum()
    hist12 = hist[hist["month"] > hist["month"].max() - pd.DateOffset(months=12)] \
        if len(hist) else hist
    headline = {
        "groups": int(len(s)),
        "pets": int(s["pets"].sum()),
        "proj_claims": float(proj_claims),
        "proj_premium": float(proj_premium),
        "proj_lr": float(proj_claims / proj_premium) if proj_premium else None,
        "hist_lr": float(hist12["claims"].sum() / hist12["premium"].sum())
        if len(hist12) else None,
        "at_risk_groups": int(s["at_risk"].sum()),
        "avg_z": float((s["z"] * s["pet_months_exp"]).sum() / pet_months) if pet_months else 0.0,
        "own_share": float((s["z"] * s["proj_claims"]).sum() / proj_claims) if proj_claims else 0.0,
        "pooled_z": float(credibility_z(pet_months, threshold)) if len(s) else 0.0,
        "pet_months": float(pet_months),
    }
    return {
        "headline": headline,
        "history": h.to_dict("records"),
        "projection": pm.drop(columns=["zc", "zp"]).to_dict("records"),
    }
