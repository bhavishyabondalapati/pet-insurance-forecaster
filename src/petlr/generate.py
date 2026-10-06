"""Phase 1 - synthetic data generator.

Creates four raw CSV files that look like messy exports from an insurer:

    groups.csv    one row per partner group (employer, breeder club, ...)
    pets.csv      one row per enrolled pet
    premiums.csv  one row per group per billing month
    claims.csv    one row per paid claim

The "true" world has seasonality, a vet-cost trend, random large claims and a
late cost shock in one region. On top of that we *deliberately* plant data
problems so the pipeline has something real to catch. What we planted is saved
to raw/_planted.json so tests can check the pipeline found all of it.

Run:  python -m petlr.generate
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from petlr import config

# --- "true world" parameters (the model never sees these directly) ---------
BASE_FREQ = 0.08  # claims per dog per month
CAT_FREQ_FACTOR = 0.75
BASE_SEVERITY = 550.0  # average normal claim, at the start of the window
LARGE_CLAIM_PROB = 0.015  # share of claims that are large (surgery, cancer)
LARGE_CLAIM_MEAN = 12_000.0
VET_COST_TREND = 0.08  # +8% per year
SEASON_AMPLITUDE = 0.12  # summer peak, winter dip
TARGET_LOSS_RATIO = 0.68  # what pricing aims for
ANNUAL_RATE_INCREASE = 0.05  # premiums go up each January
SHOCK_REGION, SHOCK_MONTHS, SHOCK_SIZE = "West", 4, 0.15  # emerging cost shock

REGION_FACTOR = {"Northeast": 1.10, "Southeast": 0.95, "Midwest": 0.90,
                 "Southwest": 0.95, "West": 1.15}
DEDUCTIBLE_FACTOR = {100: 1.15, 250: 1.00, 500: 0.82}


def last_complete_month(today: pd.Timestamp | None = None) -> pd.Timestamp:
    """First day of the last fully finished month (e.g. 2026-10-06 -> 2026-09-01)."""
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    return today.replace(day=1) - pd.DateOffset(months=1)


def true_seasonality(month_number: np.ndarray | int) -> np.ndarray:
    """Multiplier by calendar month (1-12). Peaks in July, lowest in January."""
    return 1 + SEASON_AMPLITUDE * np.sin(2 * np.pi * (np.asarray(month_number) - 4) / 12)


def _make_groups(rng: np.random.Generator, n_groups: int, months: pd.DatetimeIndex) -> pd.DataFrame:
    window_start, window_end = months[0], months[-1]
    ids = [f"G{i:04d}" for i in range(1, n_groups + 1)]

    # Most groups started before the data window, ~20% join during it.
    old_starts = pd.date_range("2015-01-01", window_start - pd.DateOffset(months=1), freq="MS")
    new_starts = months[:-3]
    is_new = rng.random(n_groups) < 0.20
    start = np.where(is_new,
                     rng.choice(new_starts, n_groups),
                     rng.choice(old_starts, n_groups))
    start = pd.to_datetime(start)

    # ~8% of groups cancel at some point inside the window.
    cancel = []
    for s in start:
        first_possible = max(s, window_start) + pd.DateOffset(months=3)
        if rng.random() < 0.08 and first_possible < window_end:
            options = pd.date_range(first_possible, window_end, freq="MS")
            cancel.append(rng.choice(options))
        else:
            cancel.append(pd.NaT)

    size = np.clip(np.round(rng.lognormal(np.log(120), 1.1, n_groups)), 10, 2000).astype(int)
    dog_share = rng.beta(5, 2, n_groups).round(3)
    region = rng.choice(config.REGIONS, n_groups)
    deductible = rng.choice(config.DEDUCTIBLES, n_groups, p=[0.25, 0.5, 0.25])
    # Hidden truths: how risky the group really is and how well it was priced.
    risk = rng.lognormal(0, 0.20, n_groups)
    pricing_error = rng.lognormal(0, 0.12, n_groups)

    return pd.DataFrame({
        "group_id": ids,
        "region": region,
        "deductible": deductible,
        "dog_share": dog_share,
        "initial_size": size,
        "start_date": start,
        "cancel_date": pd.to_datetime(cancel),
        "_risk": risk,
        "_pricing_error": pricing_error,
    })


def _expected_pppm(dog_share, region, deductible, risk):
    """Expected claims per pet per month at the start of the window (truth)."""
    species = dog_share + (1 - dog_share) * CAT_FREQ_FACTOR
    large = LARGE_CLAIM_PROB * LARGE_CLAIM_MEAN
    severity = (1 - LARGE_CLAIM_PROB) * BASE_SEVERITY + large
    return (BASE_FREQ * species * severity * REGION_FACTOR[region]
            * DEDUCTIBLE_FACTOR[deductible] * risk)


def _make_pets(rng: np.random.Generator, groups: pd.DataFrame,
               months: pd.DatetimeIndex) -> pd.DataFrame:
    """Simulate each group's enrollment month by month (joiners and leavers)."""
    rows = []
    next_id = 1
    for g in groups.itertuples():
        first = max(g.start_date, months[0])
        active: list[int] = []
        enroll_options = pd.date_range(g.start_date, max(g.start_date, months[0]), freq="MS")
        # Initial cohort.
        for _ in range(g.initial_size):
            enroll = enroll_options[rng.integers(len(enroll_options))]
            rows.append([next_id, g.group_id, rng.random() < g.dog_share, enroll, pd.NaT])
            active.append(len(rows) - 1)
            next_id += 1
        # Monthly churn.
        for m in months[months > first]:
            if pd.notna(g.cancel_date) and m >= g.cancel_date:
                break
            n_leave = rng.binomial(len(active), 0.012) if len(active) > 10 else 0
            for idx in sorted(rng.choice(len(active), n_leave, replace=False), reverse=True):
                rows[active[idx]][4] = m
                active.pop(idx)
            for _ in range(rng.poisson(len(active) * 0.015)):
                rows.append([next_id, g.group_id, rng.random() < g.dog_share, m, pd.NaT])
                active.append(len(rows) - 1)
                next_id += 1
        # True termination when the group cancels.
        if pd.notna(g.cancel_date):
            for idx in active:
                rows[idx][4] = g.cancel_date

    pets = pd.DataFrame(rows, columns=["pet_id", "group_id", "is_dog", "enroll_date", "term_date"])
    pets["pet_id"] = pets["pet_id"].map(lambda i: f"P{i:06d}")
    pets["species"] = np.where(pets.pop("is_dog"), "dog", "cat")
    pets["enroll_date"] = pd.to_datetime(pets["enroll_date"])
    pets["term_date"] = pd.to_datetime(pets["term_date"])
    return pets[["pet_id", "group_id", "species", "enroll_date", "term_date"]]


def _exposure(pets: pd.DataFrame, months: pd.DatetimeIndex) -> pd.DataFrame:
    """One row per pet per month it was truly covered."""
    parts = []
    for m in months:
        on = (pets["enroll_date"] <= m) & (pets["term_date"].isna() | (pets["term_date"] > m))
        p = pets.loc[on, ["pet_id", "group_id", "species"]].copy()
        p["month"] = m
        parts.append(p)
    return pd.concat(parts, ignore_index=True)


def _make_premiums(groups: pd.DataFrame, exposure: pd.DataFrame,
                   months: pd.DatetimeIndex) -> pd.DataFrame:
    counts = exposure.groupby(["group_id", "month"]).size().rename("pets_billed").reset_index()
    g = groups.set_index("group_id")
    rate = {
        gid: _expected_pppm(r.dog_share, r.region, r.deductible, 1.0)
        * r._pricing_error / TARGET_LOSS_RATIO
        for gid, r in g.iterrows()
    }
    years_in = counts["month"].dt.year - months[0].year
    counts["premium"] = (counts["group_id"].map(rate) * counts["pets_billed"]
                         * (1 + ANNUAL_RATE_INCREASE) ** years_in).round(2)
    return counts.rename(columns={"month": "billing_month"})


def _make_claims(rng: np.random.Generator, groups: pd.DataFrame, exposure: pd.DataFrame,
                 months: pd.DatetimeIndex, extract_date: pd.Timestamp) -> pd.DataFrame:
    g = groups.set_index("group_id")
    e = exposure
    risk = e["group_id"].map(g["_risk"]).to_numpy()
    region = e["group_id"].map(g["region"])
    region_f = region.map(REGION_FACTOR).to_numpy()
    ded_f = e["group_id"].map(g["deductible"]).map(DEDUCTIBLE_FACTOR).to_numpy()
    season = true_seasonality(e["month"].dt.month.to_numpy())
    t = ((e["month"].dt.year - months[0].year) * 12
         + e["month"].dt.month - months[0].month).to_numpy()
    trend = (1 + VET_COST_TREND) ** (t / 12)
    shock = np.where((region == SHOCK_REGION).to_numpy() & (t >= len(months) - SHOCK_MONTHS),
                     1 + SHOCK_SIZE, 1.0)
    species_f = np.where(e["species"].to_numpy() == "dog", 1.0, CAT_FREQ_FACTOR)

    lam = BASE_FREQ * species_f * season * np.sqrt(risk)
    n = rng.poisson(lam)
    idx = np.repeat(np.arange(len(e)), n)
    k = len(idx)

    sev_mult = (region_f * ded_f * trend * shock * np.sqrt(risk))[idx]
    is_large = rng.random(k) < LARGE_CLAIM_PROB
    normal = rng.lognormal(np.log(BASE_SEVERITY) - 0.5 * 0.8**2, 0.8, k)
    large = rng.lognormal(np.log(LARGE_CLAIM_MEAN) - 0.5 * 0.6**2, 0.6, k)
    amount = np.where(is_large, large, normal) * sev_mult

    month = e["month"].to_numpy()[idx]
    service = pd.to_datetime(month) + pd.to_timedelta(rng.integers(0, 28, k), unit="D")
    # Payment lag: most paid within ~2 months, ~10% are paid late (2-7 months).
    bucket = rng.choice(3, k, p=[0.6, 0.3, 0.1])
    lag = np.select([bucket == 0, bucket == 1],
                    [rng.integers(3, 26, k), rng.integers(26, 56, k)],
                    rng.integers(60, 200, k))
    paid = service + pd.to_timedelta(lag, unit="D")

    claims = pd.DataFrame({
        "pet_id": e["pet_id"].to_numpy()[idx],
        "group_id": e["group_id"].to_numpy()[idx],
        "service_date": service,
        "paid_date": paid,
        "amount": amount.round(2),
    })
    # Claims not yet paid by the extract date are not in the file (yet).
    claims = claims[claims["paid_date"] <= extract_date].reset_index(drop=True)
    claims.insert(0, "claim_id", [f"C{i:07d}" for i in range(1, len(claims) + 1)])
    return claims


def _plant_problems(rng: np.random.Generator, raw: dict[str, pd.DataFrame],
                    groups_truth: pd.DataFrame, extract_date: pd.Timestamp) -> dict:
    """Break the clean data in known, countable ways."""
    planted: dict = {}
    claims, premiums, pets, groups = (raw[k] for k in ("claims", "premiums", "pets", "groups"))

    # 1. Duplicate rows (a re-sent file batch).
    dup_c = claims.sample(frac=0.012, random_state=1)
    dup_p = premiums.sample(frac=0.005, random_state=2)
    claims = pd.concat([claims, dup_c]).sample(frac=1, random_state=3).reset_index(drop=True)
    premiums = pd.concat([premiums, dup_p]).reset_index(drop=True)
    planted["duplicate_claim_rows"] = len(dup_c)
    planted["duplicate_premium_rows"] = len(dup_p)

    # 2. Pets of cancelled groups still listed as active (term_date wiped).
    cancelled = groups_truth.loc[groups_truth["cancel_date"].notna(), ["group_id", "cancel_date"]]
    cancel_by_group = dict(zip(cancelled["group_id"], cancelled["cancel_date"]))
    group_cancel = pd.to_datetime(pets["group_id"].map(cancel_by_group))
    hit = pets["group_id"].isin(cancelled["group_id"]) & (pets["term_date"] == group_cancel)
    pets.loc[hit, "term_date"] = pd.NaT
    planted["cancelled_group_pets_still_listed"] = int(hit.sum())
    planted["cancelled_groups"] = len(cancelled)

    # 3. Late-paid claims (paid 60+ days after service) - created by the lag model.
    lag_days = (claims["paid_date"] - claims["service_date"]).dt.days
    planted["late_paid_claims"] = int((lag_days >= 60).sum())

    # 4. Dates shifted back by one day (a timezone bug turns 1 Mar into 28 Feb).
    shift_p = rng.random(len(premiums)) < 0.03
    premiums.loc[shift_p, "billing_month"] -= pd.Timedelta(days=1)
    shift_e = (rng.random(len(pets)) < 0.02)
    pets.loc[shift_e, "enroll_date"] -= pd.Timedelta(days=1)
    planted["premium_dates_shifted"] = int(shift_p.sum())
    planted["pet_enroll_dates_shifted"] = int(shift_e.sum())

    # 5. A few bad group start dates.
    bad_ids = rng.choice(groups["group_id"], 7, replace=False)
    bad_values = [pd.Timestamp("1900-01-01")] * 3 + [pd.Timestamp("2099-01-01")] * 2
    for gid, value in zip(bad_ids[:5], bad_values):
        groups.loc[groups["group_id"] == gid, "start_date"] = value
    for gid in bad_ids[5:]:  # start date after the group's first bill
        first_bill = premiums.loc[premiums["group_id"] == gid, "billing_month"].min()
        groups.loc[groups["group_id"] == gid, "start_date"] = (
            first_bill.to_period("M").to_timestamp() + pd.DateOffset(months=8))
    planted["bad_start_dates"] = sorted(bad_ids.tolist())

    raw.update(claims=claims, premiums=premiums, pets=pets, groups=groups)
    return planted


def generate(n_groups: int = 300, n_months: int = 24, end_month=None,
             seed: int = config.SEED) -> tuple[dict[str, pd.DataFrame], dict]:
    """Build all raw tables in memory. Returns (tables, planted_problems)."""
    rng = np.random.default_rng(seed)
    end = pd.Timestamp(end_month) if end_month else last_complete_month()
    end = end.to_period("M").to_timestamp()
    months = pd.date_range(end - pd.DateOffset(months=n_months - 1), end, freq="MS")
    extract_date = end + pd.offsets.MonthEnd(0)

    groups_truth = _make_groups(rng, n_groups, months)
    pets = _make_pets(rng, groups_truth, months)
    exposure = _exposure(pets, months)
    premiums = _make_premiums(groups_truth, exposure, months)
    claims = _make_claims(rng, groups_truth, exposure, months, extract_date)

    groups = groups_truth.drop(columns=["_risk", "_pricing_error", "initial_size"])
    raw = {"groups": groups, "pets": pets.copy(), "premiums": premiums, "claims": claims}
    planted = _plant_problems(rng, raw, groups_truth, extract_date)
    planted["extract_date"] = extract_date.date().isoformat()
    planted["months"] = [months[0].date().isoformat(), months[-1].date().isoformat()]
    return raw, planted


def write_raw(raw: dict[str, pd.DataFrame], planted: dict, out_dir: Path = config.RAW_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, df in raw.items():
        df.to_csv(out_dir / f"{name}.csv", index=False, date_format="%Y-%m-%d")
    (out_dir / "_planted.json").write_text(json.dumps(planted, indent=2))
    return out_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic raw data")
    parser.add_argument("--groups", type=int, default=300)
    parser.add_argument("--months", type=int, default=24)
    parser.add_argument("--end-month", default=None, help="e.g. 2026-09 (default: last full month)")
    parser.add_argument("--seed", type=int, default=config.SEED)
    args = parser.parse_args()
    raw, planted = generate(args.groups, args.months, args.end_month, args.seed)
    path = write_raw(raw, planted)
    print(f"Wrote raw data to {path}")
    for k, v in planted.items():
        print(f"  planted {k}: {v}")


if __name__ == "__main__":
    main()
