"""Phase 2 - data-quality gate built on pandera.

Two layers:

1. Raw checks describe what *perfect* raw data would look like. Real data never
   is, so every failure becomes an "issue" in the report. Known issue types are
   fixed by the cleaning step; if one is far bigger than we tolerate (e.g. half
   the claims are duplicates) the gate fails anyway, because that smells like a
   broken extract rather than normal mess.

2. Clean checks describe the dataset the model is allowed to use. Any failure
   here stops the pipeline with a DataQualityError that lists every problem.

All schemas run with lazy=True so pandera collects *every* failure instead of
stopping at the first one.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd
import pandera.pandas as pa
from pandera.errors import SchemaError, SchemaErrors

from petlr import config

MIN_START = pd.Timestamp("2000-01-01")


@dataclass
class Issue:
    """One kind of problem found in one table."""

    table: str
    check: str
    rows: int
    severity: str = "fixed"  # fixed | warning | fatal
    action: str = ""
    examples: list = field(default_factory=list)


@dataclass
class QualityReport:
    raw_issues: list[Issue] = field(default_factory=list)
    clean_failures: list[Issue] = field(default_factory=list)
    row_counts: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.clean_failures and not any(i.severity == "fatal" for i in self.raw_issues)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "row_counts": self.row_counts,
            "raw_issues": [asdict(i) for i in self.raw_issues],
            "clean_failures": [asdict(i) for i in self.clean_failures],
        }

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str))
        return path

    def summary(self) -> str:
        lines = [f"Data-quality gate: {'PASSED' if self.passed else 'FAILED'}"]
        for title, issues in [("Raw issues caught", self.raw_issues),
                              ("Clean-data failures", self.clean_failures)]:
            lines.append(f"{title}: {len(issues)}")
            for i in issues:
                lines.append(f"  [{i.severity:7}] {i.table}.{i.check}: {i.rows} rows"
                             + (f" -> {i.action}" if i.action else ""))
        return "\n".join(lines)


class DataQualityError(Exception):
    """Raised when the gate fails. Carries the full report."""

    def __init__(self, report: QualityReport):
        self.report = report
        super().__init__(report.summary())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _first_of_month(s: pd.Series) -> pd.Series:
    return s.isna() | (s.dt.day == 1)


def run_schema(schema: pa.DataFrameSchema, df: pd.DataFrame, table: str) -> list[Issue]:
    """Validate lazily and turn every failure into an Issue (grouped by check)."""
    try:
        schema.validate(df, lazy=True)
        return []
    except SchemaErrors as err:
        cases = err.failure_cases
    except SchemaError as err:  # non-lazy style errors (rare)
        return [Issue(table, str(err.check), len(df), examples=[str(err)[:200]])]

    issues = []
    cases = cases.copy()
    # Table-wide checks are reported once per column by pandera; collapse them.
    table_wide = cases["schema_context"] == "DataFrameSchema"
    cases["column"] = cases["column"].astype(object).where(~table_wide, "").fillna("")
    # "greater_than_or_equal_to(2000-01-01)" -> "greater_than_or_equal_to"
    cases["check"] = cases["check"].astype(str).str.replace(r"\(.*\)$", "", regex=True)
    for (column, check), grp in cases.groupby(["column", "check"], dropna=False, sort=False):
        idx = grp["index"].dropna()
        rows = idx.nunique() if len(idx) else len(grp)
        name = f"{column}:{check}" if column else str(check)
        examples = grp["failure_case"].astype(str).head(5).tolist()
        issues.append(Issue(table, name, int(rows), examples=examples))
    return issues


# ---------------------------------------------------------------------------
# Raw schemas: what perfect raw data would look like
# ---------------------------------------------------------------------------
def raw_schemas(as_of: pd.Timestamp) -> dict[str, pa.DataFrameSchema]:
    return {
        "claims": pa.DataFrameSchema(
            {
                "claim_id": pa.Column(str, unique=True, report_duplicates="exclude_first",
                                      description="duplicate rows share a claim_id"),
                "amount": pa.Column(float, pa.Check.gt(0)),
                "service_date": pa.Column("datetime64[ns]", pa.Check.le(as_of)),
                "paid_date": pa.Column("datetime64[ns]", pa.Check.le(as_of)),
            },
            checks=[
                pa.Check(lambda d: (d["paid_date"] - d["service_date"]).dt.days < 60,
                         name="paid_within_60_days"),
                pa.Check(lambda d: d["paid_date"] >= d["service_date"],
                         name="paid_after_service"),
            ],
        ),
        "premiums": pa.DataFrameSchema(
            {
                "billing_month": pa.Column("datetime64[ns]",
                                           pa.Check(_first_of_month, name="first_of_month")),
                "pets_billed": pa.Column(int, pa.Check.gt(0)),
                "premium": pa.Column(float, pa.Check.gt(0)),
            },
            unique=["group_id", "billing_month"],
            report_duplicates="exclude_first",
        ),
        "pets": pa.DataFrameSchema(
            {
                "pet_id": pa.Column(str, unique=True),
                "species": pa.Column(str, pa.Check.isin(["dog", "cat"])),
                "enroll_date": pa.Column("datetime64[ns]",
                                         pa.Check(_first_of_month, name="first_of_month")),
            },
            checks=[
                pa.Check(lambda d: d["group_cancel_date"].isna() | d["term_date"].notna(),
                         name="cancelled_group_pet_has_term_date"),
            ],
        ),
        "groups": pa.DataFrameSchema(
            {
                "group_id": pa.Column(str, pa.Check.str_matches(r"^G\d{4}$"), unique=True),
                "region": pa.Column(str, pa.Check.isin(config.REGIONS)),
                "deductible": pa.Column(int, pa.Check.isin(config.DEDUCTIBLES)),
                "start_date": pa.Column("datetime64[ns]", [pa.Check.ge(MIN_START),
                                                           pa.Check.le(as_of)]),
            },
            checks=[
                pa.Check(lambda d: d["first_bill"].isna() | (d["start_date"] <= d["first_bill"]),
                         name="start_before_first_bill"),
            ],
        ),
    }


# What the cleaning step does about each raw problem, and how many rows we
# tolerate before deciding the extract itself is broken (share of the table).
RAW_ACTIONS = {
    "claim_id:field_uniqueness": ("dropped exact duplicate rows", 0.05),
    "paid_within_60_days": ("kept; booked to service month, recent months grossed up "
                            "with completion factors", 0.25),
    "paid_after_service": ("dropped (impossible dates)", 0.01),
    "service_date:less_than_or_equal_to": ("dropped (after extract date)", 0.01),
    "paid_date:less_than_or_equal_to": ("dropped (after extract date)", 0.01),
    "billing_month:first_of_month": ("shifted to the 1st (one-day timezone shift)", 0.10),
    "multiple_fields_uniqueness": ("dropped exact duplicate rows", 0.05),
    "enroll_date:first_of_month": ("shifted to the 1st (one-day timezone shift)", 0.10),
    "cancelled_group_pet_has_term_date": ("terminated at the group's cancel date", 0.20),
    "start_date:greater_than_or_equal_to": ("replaced with first observed enrollment month",
                                            0.05),
    "start_date:less_than_or_equal_to": ("replaced with first observed enrollment month", 0.05),
    "start_before_first_bill": ("replaced with first observed enrollment month", 0.05),
}


def classify_raw_issues(issues: list[Issue], table_sizes: dict[str, int]) -> list[Issue]:
    """Attach the cleaning action and mark issues fatal if they exceed tolerance."""
    for issue in issues:
        known = RAW_ACTIONS.get(issue.check)
        if known is None:
            issue.severity, issue.action = "fatal", "UNKNOWN problem type - investigate"
            continue
        action, tolerance = known
        issue.action = action
        share = issue.rows / max(table_sizes.get(issue.table, 1), 1)
        if share > tolerance:
            issue.severity = "fatal"
            issue.action += f" (but {share:.1%} of rows exceeds the {tolerance:.0%} tolerance)"
        elif issue.check == "paid_within_60_days":
            issue.severity = "warning"
    return issues


# ---------------------------------------------------------------------------
# Clean schemas: what the model is allowed to consume
# ---------------------------------------------------------------------------
def clean_schemas(window_start: pd.Timestamp, as_of: pd.Timestamp) -> dict[str, pa.DataFrameSchema]:
    month = pa.Column("datetime64[ns]", [
        pa.Check(lambda s: s.dt.day == 1, name="first_of_month"),
        pa.Check.in_range(window_start, as_of),
    ])
    return {
        "monthly": pa.DataFrameSchema(
            {
                "group_id": pa.Column(str, pa.Check.str_matches(r"^G\d{4}$")),
                "month": month,
                "pet_months": pa.Column(int, pa.Check.gt(0)),
                "premium": pa.Column(float, pa.Check.gt(0)),
                "claims": pa.Column(float, pa.Check.ge(0)),
                "claims_capped": pa.Column(float, pa.Check.ge(0)),
                "claims_excess": pa.Column(float, pa.Check.ge(0)),
                "completion_factor": pa.Column(float, pa.Check.in_range(0.05, 1.0)),
                "region": pa.Column(str, pa.Check.isin(config.REGIONS)),
                "deductible": pa.Column(int, pa.Check.isin(config.DEDUCTIBLES)),
                "species_mix": pa.Column(str, pa.Check.isin(config.SPECIES_MIXES)),
                "tenure_months": pa.Column(int, pa.Check.ge(0)),
            },
            checks=[
                # Reconciliation: every pet we bill must be a pet we cover, and
                # vice versa. Pets of cancelled groups would break this.
                pa.Check(lambda d: d["pets_billed"] == d["pet_months"],
                         name="billed_pets_match_enrolled_pets"),
                pa.Check(lambda d: (d["claims_capped"] + d["claims_excess"] - d["claims"]).abs()
                         < 0.01, name="capped_plus_excess_equals_total"),
            ],
            unique=["group_id", "month"],
            strict=False,
        ),
        "claims": pa.DataFrameSchema(
            {
                "claim_id": pa.Column(str, unique=True),
                "amount": pa.Column(float, pa.Check.gt(0)),
                "paid_date": pa.Column("datetime64[ns]", pa.Check.le(as_of)),
            },
            checks=[pa.Check(lambda d: d["paid_date"] >= d["service_date"],
                             name="paid_after_service")],
        ),
        "groups": pa.DataFrameSchema(
            {
                "group_id": pa.Column(str, unique=True),
                "start_date": pa.Column("datetime64[ns]", pa.Check.in_range(MIN_START, as_of)),
            },
            checks=[pa.Check(lambda d: d["cancel_date"].isna()
                             | (d["cancel_date"] > d["start_date"]),
                             name="cancel_after_start")],
        ),
    }
