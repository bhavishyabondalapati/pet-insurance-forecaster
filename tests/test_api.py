import io

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from petlr import backtest
from petlr.api import create_app
from petlr.pipeline import save_clean


@pytest.fixture(scope="module")
def client(cleaned, tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("data")
    save_clean(cleaned, data_dir / "clean")
    cleaned.report.write(data_dir / "out" / "quality_report.json")
    backtest.run(data_dir / "clean", data_dir / "out" / "backtest.json")
    return TestClient(create_app(data_dir))


def test_health_and_meta(client):
    assert client.get("/api/health").json()["status"] == "ok"
    meta = client.get("/api/meta").json()
    assert meta["as_of"] == "2026-09-30"
    assert set(meta["filters"]) == {"species_mix", "region", "deductible", "tenure", "size"}
    assert len(meta["context"]["seasonality"]) == 12


def test_segment_view_has_credibility_everywhere(client):
    body = client.get("/api/segments").json()
    h = body["headline"]
    for key in ("proj_lr", "proj_claims", "avg_z", "own_share", "pooled_z"):
        assert key in h
    assert 0 < h["avg_z"] <= 1
    assert len(body["projection"]) == 12
    assert all(0 < m["avg_z"] <= 1 for m in body["projection"])


def test_filters_narrow_the_segment(client):
    everything = client.get("/api/segments").json()["headline"]
    west = client.get("/api/segments", params={"region": "West"}).json()
    assert west["filters"] == {"region": ["West"]}
    assert 0 < west["headline"]["groups"] < everything["groups"]
    multi = client.get("/api/segments", params=[("deductible", 100), ("deductible", 500),
                                                ("size", "small")]).json()
    assert multi["headline"]["groups"] <= everything["groups"]


def test_assumptions_change_the_answer(client):
    base = client.get("/api/segments", params={"trend": 0.0}).json()["headline"]
    high = client.get("/api/segments", params={"trend": 0.2}).json()["headline"]
    cheaper = client.get("/api/segments", params={"trend": 0.0,
                                                  "premium_change": 0.1}).json()["headline"]
    assert high["proj_claims"] > base["proj_claims"]
    assert cheaper["proj_lr"] == pytest.approx(base["proj_lr"] / 1.1, rel=1e-5)
    low_bar = client.get("/api/groups", params={"at_risk_lr": 0.01, "at_risk_only": True}).json()
    assert low_bar["count"] == base["groups"]  # everyone is "at risk" at a 1% LR bar


def test_bad_assumption_is_rejected(client):
    assert client.get("/api/segments", params={"threshold": -5}).status_code == 422
    assert client.get("/api/segments/breakdown", params={"by": "colour"}).status_code == 422


def test_breakdown(client):
    rows = client.get("/api/segments/breakdown", params={"by": "deductible"}).json()["rows"]
    assert {r["value"] for r in rows} <= {100, 250, 500}
    assert all("avg_z" in r for r in rows)


def test_groups_and_group_view(client):
    groups = client.get("/api/groups", params={"sort": "-z", "limit": 5}).json()["groups"]
    assert len(groups) == 5 and groups[0]["z"] >= groups[-1]["z"]
    gid = groups[0]["group_id"]
    g = client.get(f"/api/groups/{gid}").json()
    assert g["active"] and len(g["projection"]) == 12
    assert 0 < g["summary"]["z"] <= 1
    assert client.get("/api/groups/G9999").status_code == 404


def test_csv_export(client):
    r = client.get("/api/export.csv", params={"region": "West"})
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    df = pd.read_csv(io.StringIO(r.text))
    assert (df["region"] == "West").all()
    assert {"z", "proj_lr", "at_risk", "trend_used"} <= set(df.columns)


def test_backtest_and_quality(client):
    assert "credibility" in client.get("/api/backtest").json()["models"]
    assert client.get("/api/quality").json()["passed"] is True
