"""Phase 2 - raw CSVs -> clean monthly dataset, guarded by the quality gate.

The output is one row per group per month:

    group_id, month, pet_months, premium, claims (developed), claims_capped,
    claims_excess, claim_count, completion_factor, region, deductible,
    species_mix, size_band, tenure_months, segment, ...

"Developed" claims: recent months are missing claims that will be paid later
(late payments). We estimate how complete each month is from older months
(completion factors) and gross the recent ones up.

Run:  python -m petlr.pipeline
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from petlr import config
from petlr.quality import (
    MIN_START,
    DataQualityError,
    Issue,
    QualityReport,
    classify_raw_issues,
    clean_schemas,
    raw_schemas,
    run_schema,
)

MATURE_LAG = 8  # months after which we treat a service month as fully paid
DATE_COLS = {
    "claims": ["service_date", "paid_date"],
    "premiums": ["billing_month"],
    "pets": ["enroll_date", "term_date"],
    "groups": ["start_date", "cancel_date"],
}


@dataclass
class CleanResult:
    groups: pd.DataFrame
    pets: pd.DataFrame
    claims: pd.DataFrame
    monthly: pd.DataFrame
    completion: pd.DataFrame
    report: QualityReport
    as_of: pd.Timestamp


# ---------------------------------------------------------------------------
# Loading and small helpers
# ---------------------------------------------------------------------------
def load_raw(raw_dir: Path = config.RAW_DIR) -> dict[str, pd.DataFrame]:
    raw = {}
    for name, cols in DATE_COLS.items():
        raw[name] = pd.read_csv(raw_dir / f"{name}.csv", parse_dates=cols)
    return raw


def _standardize(raw: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Same dtypes no matter whether the data came from CSV or memory."""
    out = {}
    for name, df in raw.items():
        df = df.copy()
        for c in DATE_COLS[name]:
            df[c] = pd.to_datetime(df[c]).astype("datetime64[ns]")
        for c in ("group_id", "pet_id", "claim_id", "species", "region"):
            if c in df:
                df[c] = df[c].astype(str)
        out[name] = df
    out["claims"]["amount"] = out["claims"]["amount"].astype(float)
    out["premiums"]["premium"] = out["premiums"]["premium"].astype(float)
    out["premiums"]["pets_billed"] = out["premiums"]["pets_billed"].astype(int)
    out["groups"]["deductible"] = out["groups"]["deductible"].astype(int)
    return out


def snap_to_month(dates: pd.Series) -> pd.Series:
    """Round a date to the nearest 1st of the month (fixes one-day shifts).

    2025-02-28 -> 2025-03-01, 2025-03-01 -> 2025-03-01, NaT stays NaT.
    """
    start = dates.dt.to_period("M").dt.to_timestamp()
    next_start = start + pd.DateOffset(months=1)
    snapped = dates.where(dates.isna(), start.where(dates.dt.day < 15, next_start))
    return snapped.astype("datetime64[ns]")


def month_diff(later: pd.Series, earlier: pd.Series) -> pd.Series:
    return (later.dt.year - earlier.dt.year) * 12 + (later.dt.month - earlier.dt.month)


# ---------------------------------------------------------------------------
# Main cleaning logic
# ---------------------------------------------------------------------------
def _raw_checks(raw: dict[str, pd.DataFrame], as_of: pd.Timestamp) -> list[Issue]:
    groups, pets, premiums = raw["groups"], raw["pets"], raw["premiums"]
    cancel = dict(zip(groups["group_id"], groups["cancel_date"]))
    pets_chk = pets.assign(group_cancel_date=pd.to_datetime(pets["group_id"].map(cancel)))
    first_bill = snap_to_month(premiums["billing_month"]).groupby(premiums["group_id"]).min()
    groups_chk = groups.assign(
        first_bill=pd.to_datetime(groups["group_id"].map(first_bill.to_dict())))
    frames = {"claims": raw["claims"], "premiums": premiums, "pets": pets_chk,
              "groups": groups_chk}
    issues: list[Issue] = []
    for table, schema in raw_schemas(as_of).items():
        issues += run_schema(schema, frames[table], table)
    return classify_raw_issues(issues, {k: len(v) for k, v in raw.items()})


def completion_factors(claims: pd.DataFrame, as_of_month: pd.Timestamp) -> pd.DataFrame:
    """Share of a month's claim dollars paid within `lag` months of service.

    Uses only "mature" service months (old enough that everything is paid),
    like a simplified chain-ladder. cf[0] = 0.6 means a brand-new month
    typically shows only 60% of the claims it will finally have.
    """
    age = month_diff(pd.Series(as_of_month, index=claims.index), claims["service_month"])
    mature = claims[age >= MATURE_LAG]
    lags = np.arange(MATURE_LAG + 1)
    if mature.empty:
        return pd.DataFrame({"lag": lags, "factor": 1.0})
    by_lag = mature.groupby(mature["lag_months"].clip(upper=MATURE_LAG))["amount"].sum()
    cum = by_lag.reindex(lags, fill_value=0).cumsum() / by_lag.sum()
    return pd.DataFrame({"lag": lags, "factor": cum.clip(lower=0.05).round(6).values})


def clean(raw: dict[str, pd.DataFrame], as_of: pd.Timestamp | None = None,
          strict: bool = True) -> CleanResult:
    """Run raw checks, fix known problems, build the monthly table, run the gate."""
    raw = _standardize(raw)
    if as_of is None:
        as_of = raw["claims"]["paid_date"].max().to_period("M").end_time.normalize()
    as_of = pd.Timestamp(as_of).normalize()
    as_of_month = as_of.to_period("M").to_timestamp()

    report = QualityReport()
    report.row_counts["raw"] = {k: len(v) for k, v in raw.items()}
    report.raw_issues = _raw_checks(raw, as_of)

    claims, premiums = raw["claims"], raw["premiums"]
    pets, groups = raw["pets"], raw["groups"]

    # --- Fix 1+4: snap shifted dates, then drop exact duplicate rows ---------
    premiums["billing_month"] = snap_to_month(premiums["billing_month"])
    premiums = premiums.drop_duplicates().reset_index(drop=True)
    pets["enroll_date"] = snap_to_month(pets["enroll_date"])
    pets["term_date"] = snap_to_month(pets["term_date"])
    claims = claims.drop_duplicates().reset_index(drop=True)

    # --- Impossible / future claims are dropped ------------------------------
    ok = ((claims["paid_date"] >= claims["service_date"]) & (claims["paid_date"] <= as_of)
          & (claims["service_date"] <= as_of))
    claims = claims[ok].reset_index(drop=True)

    # --- Fix 2: pets of cancelled groups end when the group ends -------------
    cancel = pd.to_datetime(pets["group_id"].map(dict(zip(groups["group_id"],
                                                            groups["cancel_date"]))))
    still_on = cancel.notna() & (pets["term_date"].isna() | (pets["term_date"] > cancel))
    pets.loc[still_on, "term_date"] = cancel[still_on]

    # --- Fix 5: bad start dates -> first month we actually see the group ------
    first_seen = pd.concat([
        pets.groupby("group_id")["enroll_date"].min(),
        premiums.groupby("group_id")["billing_month"].min(),
    ], axis=1).min(axis=1)
    observed = pd.to_datetime(groups["group_id"].map(first_seen.to_dict()))
    bad = ((groups["start_date"] < MIN_START) | (groups["start_date"] > as_of)
           | (groups["start_date"] > observed))
    groups.loc[bad, "start_date"] = observed[bad]
    groups["start_date"] = groups["start_date"].astype("datetime64[ns]")

    # --- Exposure: count covered pets on the 1st of each month ----------------
    window_start = premiums["billing_month"].min()
    months = pd.date_range(window_start, as_of_month, freq="MS")
    pets = pets.merge(groups[["group_id", "start_date"]], on="group_id", how="left")
    parts = []
    for m in months:
        on = ((pets["enroll_date"] <= m) & (pets["term_date"].isna() | (pets["term_date"] > m))
              & (pets["start_date"] <= m))
        sub = pets.loc[on]
        parts.append(pd.DataFrame({
            "group_id": sub["group_id"], "month": m, "is_dog": sub["species"] == "dog"}))
    exposure = (pd.concat(parts).groupby(["group_id", "month"])
                .agg(pet_months=("is_dog", "size"), dog_months=("is_dog", "sum")).reset_index())
    pets = pets.drop(columns="start_date")

    # --- Claims: book to the month the vet visit happened (incurred basis) ---
    claims["service_month"] = claims["service_date"].dt.to_period("M").dt.to_timestamp()
    claims["paid_month"] = claims["paid_date"].dt.to_period("M").dt.to_timestamp()
    claims["lag_months"] = month_diff(claims["paid_month"], claims["service_month"])
    claims["capped"] = claims["amount"].clip(upper=config.LARGE_CLAIM_CAP)
    claims["excess"] = claims["amount"] - claims["capped"]
    claims["is_large"] = claims["excess"] > 0

    completion = completion_factors(claims, as_of_month)
    cm = (claims.groupby(["group_id", "service_month"])
          .agg(claims_reported=("amount", "sum"), capped=("capped", "sum"),
               excess=("excess", "sum"), claim_count=("amount", "size"),
               large_claim_count=("is_large", "sum"))
          .reset_index().rename(columns={"service_month": "month"}))

    # --- Join exposure, premium and claims -----------------------------------
    monthly = exposure.merge(
        premiums.rename(columns={"billing_month": "month"}),
        on=["group_id", "month"], how="outer")
    orphan = cm.merge(monthly[["group_id", "month"]], how="left", indicator=True)
    orphan_claims = orphan[orphan["_merge"] == "left_only"]
    monthly = monthly.merge(cm, on=["group_id", "month"], how="left")
    for c in ("claims_reported", "capped", "excess", "claim_count", "large_claim_count"):
        monthly[c] = monthly[c].fillna(0)

    age = month_diff(pd.Series(as_of_month, index=monthly.index), monthly["month"])
    cf = completion.set_index("lag")["factor"]
    monthly["completion_factor"] = age.clip(upper=MATURE_LAG).map(cf).astype(float)
    monthly["claims_capped"] = monthly.pop("capped") / monthly["completion_factor"]
    monthly["claims_excess"] = monthly.pop("excess") / monthly["completion_factor"]
    monthly["claims"] = monthly["claims_capped"] + monthly["claims_excess"]

    # --- Group attributes ------------------------------------------------------
    totals = monthly.groupby("group_id")[["dog_months", "pet_months"]].sum()
    dog_share = (totals["dog_months"] / totals["pet_months"]).rename("dog_share_actual")
    latest = monthly.sort_values("month").groupby("group_id")["pet_months"].last()
    groups = groups.merge(dog_share, left_on="group_id", right_index=True, how="left")
    groups["species_mix"] = groups["dog_share_actual"].fillna(groups["dog_share"]).map(
        config.species_mix_label)
    groups["size_band"] = groups["group_id"].map(latest).fillna(0).map(config.size_band)
    groups["segment"] = groups["species_mix"] + "|" + groups["deductible"].astype(str)

    monthly = monthly.merge(
        groups[["group_id", "region", "deductible", "species_mix", "size_band", "segment",
                "start_date", "cancel_date"]], on="group_id", how="left")
    monthly["tenure_months"] = month_diff(monthly["month"], monthly["start_date"]).astype(int)
    for c in ("pet_months", "dog_months", "pets_billed", "claim_count", "large_claim_count"):
        monthly[c] = monthly[c].fillna(-1).astype(int)  # -1 makes gaps fail the gate loudly
    monthly = monthly.sort_values(["group_id", "month"]).reset_index(drop=True)

    # --- The gate ---------------------------------------------------------------
    report.row_counts["clean"] = {"claims": len(claims), "pets": len(pets),
                                  "groups": len(groups), "monthly": len(monthly)}
    schemas = clean_schemas(window_start, as_of)
    report.clean_failures += run_schema(schemas["monthly"], monthly, "monthly")
    report.clean_failures += run_schema(schemas["claims"], claims, "claims")
    report.clean_failures += run_schema(schemas["groups"], groups, "groups")
    if len(orphan_claims):
        report.clean_failures.append(Issue(
            "claims", "claim_without_covered_pet_month", len(orphan_claims),
            examples=orphan_claims[["group_id", "month"]].astype(str).head(5).values.tolist()))
    for issue in report.clean_failures:
        issue.severity = "fatal"

    result = CleanResult(groups, pets, claims, monthly, completion, report, as_of)
    if strict and not report.passed:
        raise DataQualityError(report)
    return result


# ---------------------------------------------------------------------------
# Saving / loading clean outputs
# ---------------------------------------------------------------------------
def save_clean(result: CleanResult, clean_dir: Path = config.CLEAN_DIR) -> Path:
    clean_dir.mkdir(parents=True, exist_ok=True)
    result.monthly.to_parquet(clean_dir / "monthly.parquet", index=False)
    result.claims.to_parquet(clean_dir / "claims.parquet", index=False)
    result.groups.to_parquet(clean_dir / "groups.parquet", index=False)
    result.completion.to_csv(clean_dir / "completion_factors.csv", index=False)
    (clean_dir / "meta.json").write_text(json.dumps({"as_of": result.as_of.date().isoformat()}))
    return clean_dir


def load_clean(clean_dir: Path = config.CLEAN_DIR) -> dict:
    meta = json.loads((clean_dir / "meta.json").read_text())
    return {
        "monthly": pd.read_parquet(clean_dir / "monthly.parquet"),
        "claims": pd.read_parquet(clean_dir / "claims.parquet"),
        "groups": pd.read_parquet(clean_dir / "groups.parquet"),
        "as_of": pd.Timestamp(meta["as_of"]),
    }


def run(raw_dir: Path = config.RAW_DIR, clean_dir: Path = config.CLEAN_DIR,
        report_path: Path = config.OUT_DIR / "quality_report.json") -> CleanResult:
    """Load raw, clean, ALWAYS write the report, then fail loudly if needed."""
    try:
        result = clean(load_raw(raw_dir), strict=True)
    except DataQualityError as err:
        err.report.write(report_path)
        raise
    result.report.write(report_path)
    save_clean(result, clean_dir)
    return result


def main() -> None:
    argparse.ArgumentParser(description="Clean raw data and run the quality gate").parse_args()
    result = run()
    print(result.report.summary())
    print(f"Clean monthly rows: {len(result.monthly)} -> {config.CLEAN_DIR}")


if __name__ == "__main__":
    main()
