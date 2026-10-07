"""Prefect orchestration: the whole pipeline as one scheduled flow.

    python -m petlr.flows            # run once now
    python -m petlr.flows --serve    # stay running, execute daily at 06:00

Each step is a Prefect task, so the Prefect UI shows which step failed, how
long each took, and the logs. Retries cover flaky I/O; the quality gate is
NOT retried because bad data won't fix itself.
"""

from __future__ import annotations

import argparse

from prefect import flow, get_run_logger, task

from petlr import backtest, config, generate, monitoring, pipeline
from petlr.projection import project


@task(retries=2, retry_delay_seconds=5)
def generate_raw(seed: int = config.SEED) -> str:
    raw, planted = generate.generate(seed=seed)
    path = generate.write_raw(raw, planted)
    get_run_logger().info("Raw data written to %s (planted: %s)", path,
                          {k: v for k, v in planted.items() if k != "bad_start_dates"})
    return str(path)


@task
def clean_and_gate() -> dict:
    logger = get_run_logger()
    try:
        result = pipeline.run()
    except pipeline.DataQualityError as err:
        logger.error(err.report.summary())
        raise
    logger.info(result.report.summary())
    return {"monthly_rows": len(result.monthly), "as_of": str(result.as_of.date())}


@task
def snapshot_projection() -> str:
    """Save today's default projection so we can later compare it with actuals."""
    clean = pipeline.load_clean()
    result = project(clean["monthly"])
    folder = config.OUT_DIR / "projections"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"projection_{result.context['as_of_month']}.parquet"
    result.monthly.to_parquet(path, index=False)
    lr = result.summary["proj_claims"].sum() / result.summary["proj_premium"].sum()
    get_run_logger().info("Projected 12-month loss ratio %.1f%% -> %s", 100 * lr, path)
    return str(path)


@task
def backtest_task() -> dict:
    r = backtest.run()
    cred = r["models"]["credibility"]
    get_run_logger().info("Back-test bias %+.1f%%, group WAPE %.1f%%",
                          100 * cred["portfolio"]["bias"], 100 * cred["group"]["wape"])
    return cred


@task
def monitoring_task() -> str:
    r = monitoring.run()
    logger = get_run_logger()
    for checks in r["sections"].values():
        for c in checks:
            if c["status"] != "ok":
                (logger.error if c["status"] == "fail" else logger.warning)(
                    "[%s] %s: %s", c["status"].upper(), c["name"], c["message"])
    logger.info("Monitoring overall: %s %s", r["overall"].upper(), r["counts"])
    return r["overall"]


@flow(name="loss-ratio-pipeline", log_prints=True)
def loss_ratio_pipeline(regenerate: bool = True) -> dict:
    """Synthetic 'extract' -> clean + gate -> projection snapshot -> back-test -> monitoring."""
    if regenerate:
        generate_raw()
    summary = clean_and_gate()
    summary["projection"] = snapshot_projection()
    summary["backtest"] = backtest_task()
    summary["monitoring"] = monitoring_task()
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run or schedule the pipeline")
    parser.add_argument("--serve", action="store_true", help="run daily at 06:00 (cron)")
    parser.add_argument("--cron", default="0 6 * * *")
    parser.add_argument("--no-regenerate", action="store_true")
    args = parser.parse_args()
    if args.serve:
        loss_ratio_pipeline.serve(name="daily-loss-ratio", cron=args.cron,
                                  parameters={"regenerate": not args.no_regenerate})
    else:
        print(loss_ratio_pipeline(regenerate=not args.no_regenerate))


if __name__ == "__main__":
    main()
