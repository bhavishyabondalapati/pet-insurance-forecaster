"""Phase 4 - back-test: hide the last 6 months, project them, measure the error.

The important detail is *what we knew at the cutoff*. We rebuild history using
only claims paid by the cutoff date (with completion factors estimated then),
so late payments from the future can't leak into the past.

We score three models on the same hidden months:
    credibility  - the real model (Z blend)
    own_only     - Z = 1 for everyone (trust each group fully)
    bench_only   - Z = 0 for everyone (ignore each group's experience)
If credibility doesn't beat both, the blend isn't earning its keep.

Run:  python -m petlr.backtest
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from petlr import config
from petlr.pipeline import develop_claims, load_clean
from petlr.projection import Assumptions, project

MODELS = {
    "credibility": None,  # use the given threshold
    "own_only": 1e-9,  # tiny threshold -> Z = 1
    "bench_only": 1e18,  # huge threshold -> Z ~ 0
}


def history_as_of(monthly: pd.DataFrame, claims: pd.DataFrame,
                  cutoff: pd.Timestamp) -> pd.DataFrame:
    """The monthly table exactly as it would have looked at the cutoff month."""
    hist = monthly[monthly["month"] <= cutoff].copy()
    claim_cols = [c for c in ("claims_reported", "claim_count", "large_claim_count",
                              "completion_factor", "claims_capped", "claims_excess", "claims")]
    redeveloped, _, _ = develop_claims(claims, hist, cutoff)
    hist = hist.drop(columns=claim_cols).merge(redeveloped, on=["group_id", "month"])
    return hist


def _score(expected: pd.Series, actual: pd.Series) -> dict:
    """WAPE: sum |A - E| / sum A. Bias: sum E / sum A - 1 (positive = over-projected)."""
    return {
        "wape": float((actual - expected).abs().sum() / actual.sum()),
        "bias": float(expected.sum() / actual.sum() - 1),
    }


def _credibility_band(z: pd.Series) -> pd.Series:
    return pd.cut(z, [-0.01, 0.3, 0.7, 1.0], labels=["low (Z<0.3)", "mid (0.3-0.7)",
                                                    "high (Z>=0.7)"])


def run_backtest(monthly: pd.DataFrame, claims: pd.DataFrame, holdout: int = 6,
                 a: Assumptions = Assumptions()) -> dict:
    last = monthly["month"].max()
    cutoff = last - pd.DateOffset(months=holdout)
    hist = history_as_of(monthly, claims, cutoff)
    actual = monthly[monthly["month"] > cutoff]

    out: dict = {"cutoff": cutoff.date().isoformat(), "holdout_months": holdout,
                 "models": {}}
    group_tables = {}
    for name, threshold in MODELS.items():
        a_m = replace(a, horizon=holdout,
                      credibility_threshold=threshold or a.credibility_threshold)
        res = project(hist, a_m)
        # Score the cost model on the pets that actually existed, so exposure
        # changes (joins, leavers, cancellations) don't count as model error.
        m = actual.merge(res.monthly[["group_id", "month", "pppm", "z"]],
                         on=["group_id", "month"], how="inner")
        m["expected"] = m["pppm"] * m["pet_months"]
        g = m.groupby("group_id").agg(expected=("expected", "sum"), actual=("claims", "sum"),
                                      premium=("premium", "sum"), z=("z", "first"))
        g["proj_lr"] = g["expected"] / g["premium"]
        g["actual_lr"] = g["actual"] / g["premium"]
        lr_mae = float((g["premium"] * (g["proj_lr"] - g["actual_lr"]).abs()).sum()
                       / g["premium"].sum())
        out["models"][name] = {
            "portfolio": _score(m["expected"], m["claims"]),
            "group": {**_score(g["expected"], g["actual"]), "lr_mae": lr_mae},
            "groups_scored": int(len(g)),
        }
        group_tables[name] = g
        if name == "credibility":
            out["context"] = {"annual_trend": res.context["annual_trend"],
                              "large_claim_load": res.context["large_claim_load"]}
            by_month = m.groupby("month").agg(expected=("expected", "sum"),
                                              actual=("claims", "sum"),
                                              premium=("premium", "sum")).reset_index()
            by_month["a_to_e"] = by_month["actual"] / by_month["expected"]
            by_month["proj_lr"] = by_month["expected"] / by_month["premium"]
            by_month["actual_lr"] = by_month["actual"] / by_month["premium"]
            by_month["month"] = by_month["month"].dt.date.astype(str)
            out["by_month"] = by_month.round(4).to_dict("records")

    # Where does credibility help? Compare models by the groups' Z band.
    cred = group_tables["credibility"]
    band = _credibility_band(cred["z"])
    by_band = []
    for label in band.cat.categories:
        ids = band.index[band == label]
        row = {"band": str(label), "groups": int(len(ids))}
        for name, g in group_tables.items():
            sub = g.loc[g.index.intersection(ids)]
            row[name] = _score(sub["expected"], sub["actual"])["wape"] if len(sub) else None
        by_band.append(row)
    out["by_credibility_band"] = by_band

    groups = cred.reset_index()
    groups["abs_error_pct"] = (groups["expected"] / groups["actual"].replace(0, np.nan) - 1)
    out["groups"] = groups.round(4).to_dict("records")
    return out


def run(clean_dir: Path = config.CLEAN_DIR, out_path: Path = config.OUT_DIR / "backtest.json",
        holdout: int = 6) -> dict:
    clean = load_clean(clean_dir)
    result = run_backtest(clean["monthly"], clean["claims"], holdout)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, default=str))
    return result


def main() -> None:
    r = run()
    print(f"Back-test: cutoff {r['cutoff']}, {r['holdout_months']} hidden months")
    print(f"{'model':12} {'portfolio bias':>15} {'group WAPE':>11} {'LR MAE':>8}")
    for name, m in r["models"].items():
        print(f"{name:12} {m['portfolio']['bias']:>+15.1%} {m['group']['wape']:>11.1%}"
              f" {m['group']['lr_mae']:>8.3f}")
    print("\nGroup WAPE by credibility band:")
    for b in r["by_credibility_band"]:
        cells = " ".join(f"{k}={b[k]:.1%}" for k in MODELS if b[k] is not None)
        print(f"  {b['band']:14} ({b['groups']:3} groups) {cells}")


if __name__ == "__main__":
    main()
