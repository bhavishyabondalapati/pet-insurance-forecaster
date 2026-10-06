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

from petlr import config, generate, pipeline


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


@flow(name="loss-ratio-pipeline", log_prints=True)
def loss_ratio_pipeline(regenerate: bool = True) -> dict:
    """Synthetic 'extract' -> clean + quality gate."""
    if regenerate:
        generate_raw()
    summary = clean_and_gate()
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
