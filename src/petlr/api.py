"""Phase 5 - FastAPI service.

    uvicorn petlr.api:app --reload          # http://localhost:8000/api/docs

Every endpoint takes the model assumptions as query parameters, so the website
can re-run the projection live as sliders move:

    trend            annual vet-cost trend (blank = estimated from data)
    threshold        pet-months needed for full credibility
    premium_change   rate change on projected premium (0.05 = +5%)
    at_risk_lr       projected loss ratio that flags a group
    seasonality      apply the seasonal pattern (true/false)
"""

from __future__ import annotations

import io
import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Annotated

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from petlr import config
from petlr.pipeline import load_clean
from petlr.projection import Assumptions, ProjectionResult, aggregate, project

DEFAULTS = Assumptions()
DIMENSIONS = {"region": "region", "species_mix": "species_mix", "deductible": "deductible",
              "size": "size_band", "tenure": "tenure_band"}


# ---------------------------------------------------------------------------
# Data store: loads the clean data once, reloads when the pipeline re-runs
# ---------------------------------------------------------------------------
class Store:
    def __init__(self, data_dir: Path):
        self.clean_dir = data_dir / "clean"
        self.out_dir = data_dir / "out"
        self._version: float | None = None
        self._data: dict | None = None
        self._project = lru_cache(maxsize=64)(self._project_uncached)

    def _meta_mtime(self) -> float | None:
        meta = self.clean_dir / "meta.json"
        return meta.stat().st_mtime if meta.exists() else None

    @property
    def data(self) -> dict:
        version = self._meta_mtime()
        if version is None:
            raise HTTPException(503, "No clean data yet - run the pipeline first "
                                     "(python -m petlr.flows).")
        if version != self._version:
            self._data = load_clean(self.clean_dir)
            self._version = version
            self._project.cache_clear()
        return self._data

    def _project_uncached(self, a: Assumptions, version: float) -> ProjectionResult:
        result = project(self.data["monthly"], a)
        s = result.summary
        s["tenure_band"] = s["tenure_months"].map(config.tenure_band)
        return result

    def projection(self, a: Assumptions) -> ProjectionResult:
        self.data  # reloads the files (and clears the cache) if the pipeline re-ran
        return self._project(a, self._version)

    def read_json(self, name: str) -> dict:
        path = self.out_dir / name
        if not path.exists():
            raise HTTPException(404, f"{name} not found - run the pipeline first.")
        return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Query parameter models
# ---------------------------------------------------------------------------
def assumptions(
    trend: Annotated[float | None, Query(ge=-0.2, le=0.5,
                                         description="annual trend; blank = estimated")] = None,
    threshold: Annotated[float, Query(gt=0, le=100_000)] = DEFAULTS.credibility_threshold,
    premium_change: Annotated[float, Query(ge=-0.5, le=1.0)] = DEFAULTS.premium_change,
    at_risk_lr: Annotated[float, Query(gt=0, le=3)] = DEFAULTS.at_risk_lr,
    seasonality: bool = True,
    horizon: Annotated[int, Query(ge=1, le=24)] = DEFAULTS.horizon,
) -> Assumptions:
    return Assumptions(annual_trend=trend, credibility_threshold=threshold,
                       premium_change=premium_change, at_risk_lr=at_risk_lr,
                       horizon=horizon, use_seasonality=seasonality)


class Filters:
    def __init__(
        self,
        species_mix: Annotated[list[str] | None, Query()] = None,
        region: Annotated[list[str] | None, Query()] = None,
        deductible: Annotated[list[int] | None, Query()] = None,
        tenure: Annotated[list[str] | None, Query(description="new|established|mature")] = None,
        size: Annotated[list[str] | None, Query(description="small|medium|large|jumbo")] = None,
    ):
        self.values = {"species_mix": species_mix, "region": region, "deductible": deductible,
                       "tenure": tenure, "size": size}

    def apply(self, summary: pd.DataFrame) -> pd.DataFrame:
        mask = pd.Series(True, index=summary.index)
        for key, wanted in self.values.items():
            if wanted:
                mask &= summary[DIMENSIONS[key]].isin(wanted)
        return summary[mask]

    def active(self) -> dict:
        return {k: v for k, v in self.values.items() if v}


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------
def _clean_value(v):
    if isinstance(v, (pd.Timestamp,)):
        return v.date().isoformat()
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return None if math.isnan(v) or math.isinf(v) else round(float(v), 6)
    if v is pd.NaT:
        return None
    return v


def records(rows) -> list[dict]:
    if isinstance(rows, pd.DataFrame):
        rows = rows.to_dict("records")
    return [{k: _clean_value(v) for k, v in r.items()} for r in rows]


def clean_dict(d: dict) -> dict:
    return {k: _clean_value(v) for k, v in d.items()}


GROUP_COLUMNS = ["group_id", "segment", "region", "species_mix", "deductible", "size_band",
                 "tenure_band", "tenure_months", "pets", "pet_months_exp", "z", "own_pppm",
                 "bench_pppm", "blended_pppm", "expected_pppm", "proj_claims", "proj_premium",
                 "proj_lr", "hist_lr", "at_risk"]


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
def create_app(data_dir: Path | None = None) -> FastAPI:
    store = Store(Path(data_dir) if data_dir else config.DATA_DIR)
    app = FastAPI(title="Pet Insurance Loss-Ratio Forecaster",
                  description="Credibility-weighted loss-ratio projections on synthetic data.",
                  docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"],
                       allow_methods=["GET"], allow_headers=["*"])
    api = APIRouter(prefix="/api")
    app.state.store = store

    @api.get("/health")
    def health():
        ok = store._meta_mtime() is not None
        return {"status": "ok" if ok else "no-data",
                "as_of": store.data["as_of"].date().isoformat() if ok else None}

    @api.get("/meta")
    def meta(a: Assumptions = Depends(assumptions)):
        data, res = store.data, store.projection(a)
        s = res.summary
        return {
            "as_of": data["as_of"].date().isoformat(),
            "history_start": data["monthly"]["month"].min().date().isoformat(),
            "groups_total": int(data["monthly"]["group_id"].nunique()),
            "groups_active": int(len(s)),
            "large_claim_cap": config.LARGE_CLAIM_CAP,
            "defaults": {"trend": None, "threshold": DEFAULTS.credibility_threshold,
                         "premium_change": DEFAULTS.premium_change,
                         "at_risk_lr": DEFAULTS.at_risk_lr, "seasonality": True},
            "context": {k: v for k, v in res.context.items() if k != "assumptions"},
            "filters": {
                "species_mix": config.SPECIES_MIXES, "region": config.REGIONS,
                "deductible": config.DEDUCTIBLES, "tenure": config.TENURE_BANDS,
                "size": config.SIZE_BANDS,
            },
        }

    @api.get("/segments")
    def segment_view(a: Assumptions = Depends(assumptions), f: Filters = Depends()):
        """History + projection for all groups matching the filters."""
        res = store.projection(a)
        chosen = f.apply(res.summary)
        out = aggregate(store.data["monthly"], res, chosen["group_id"], a.credibility_threshold)
        return {
            "filters": f.active(),
            "assumptions": res.context["assumptions"],
            "trend_used": res.context["annual_trend"],
            "trend_source": res.context["trend_source"],
            "headline": clean_dict(out["headline"]),
            "history": records(out["history"]),
            "projection": records(out["projection"]),
        }

    @api.get("/segments/breakdown")
    def segment_breakdown(by: Annotated[str, Query(pattern="^(region|species_mix|deductible|"
                                                         "size|tenure)$")] = "region",
                          a: Assumptions = Depends(assumptions), f: Filters = Depends()):
        """Headline numbers per value of one dimension (e.g. per region)."""
        res = store.projection(a)
        chosen = f.apply(res.summary)
        rows = []
        for value, grp in chosen.groupby(DIMENSIONS[by]):
            h = aggregate(store.data["monthly"], res, grp["group_id"],
                          a.credibility_threshold)["headline"]
            rows.append({"value": _clean_value(value), **clean_dict(h)})
        return {"by": by, "rows": rows}

    @api.get("/groups")
    def groups(a: Assumptions = Depends(assumptions), f: Filters = Depends(),
               at_risk_only: bool = False,
               sort: Annotated[str, Query(pattern="^-?[a-z_]+$")] = "-proj_lr",
               limit: Annotated[int, Query(ge=1, le=1000)] = 500):
        s = f.apply(store.projection(a).summary)
        if at_risk_only:
            s = s[s["at_risk"]]
        col = sort.lstrip("-")
        if col in s.columns:
            s = s.sort_values(col, ascending=not sort.startswith("-"))
        return {"count": int(len(s)), "groups": records(s[GROUP_COLUMNS].head(limit))}

    @api.get("/groups/{group_id}")
    def group_view(group_id: str, a: Assumptions = Depends(assumptions)):
        monthly = store.data["monthly"]
        hist = monthly[monthly["group_id"] == group_id]
        if hist.empty:
            raise HTTPException(404, f"Unknown group {group_id}")
        res = store.projection(a)
        s = res.summary[res.summary["group_id"] == group_id]
        h = hist[["month", "pet_months", "premium", "claims", "claims_reported",
                  "completion_factor", "claim_count", "large_claim_count"]].copy()
        h["loss_ratio"] = h["claims"] / h["premium"]
        h["pppm"] = h["claims"] / h["pet_months"]
        first = hist.iloc[0]
        return {
            "group_id": group_id,
            "active": not s.empty,
            "cancel_date": _clean_value(hist["cancel_date"].iloc[0]),
            "attributes": clean_dict({k: first[k] for k in ("region", "species_mix",
                                                            "deductible", "segment")}),
            "summary": records(s[GROUP_COLUMNS])[0] if not s.empty else None,
            "large_claim_load": res.context["large_claim_load"],
            "history": records(h),
            "projection": records(res.monthly[res.monthly["group_id"] == group_id]),
        }

    @api.get("/export.csv")
    def export_csv(a: Assumptions = Depends(assumptions), f: Filters = Depends()):
        res = store.projection(a)
        s = f.apply(res.summary)[GROUP_COLUMNS].copy()
        s["trend_used"] = res.context["annual_trend"]
        s["credibility_threshold"] = a.credibility_threshold
        s["premium_change"] = a.premium_change
        s["as_of_month"] = res.context["as_of_month"]
        buf = io.StringIO()
        s.round(4).to_csv(buf, index=False)
        name = f"loss_ratio_projection_{res.context['as_of_month']}.csv"
        return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @api.get("/backtest")
    def backtest_view():
        return store.read_json("backtest.json")

    @api.get("/quality")
    def quality_view():
        return store.read_json("quality_report.json")

    app.include_router(api)
    return app


app = create_app()
