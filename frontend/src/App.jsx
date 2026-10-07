import { useEffect, useMemo, useState } from "react";
import { buildQuery, exportUrl, getJSON } from "./api.js";
import { monthLabel, pct } from "./format.js";
import Backtest from "./components/Backtest.jsx";
import Breakdown from "./components/Breakdown.jsx";
import Filters from "./components/Filters.jsx";
import GroupDetail from "./components/GroupDetail.jsx";
import GroupsTable from "./components/GroupsTable.jsx";
import LossRatioChart from "./components/LossRatioChart.jsx";
import Monitoring from "./components/Monitoring.jsx";
import Sliders from "./components/Sliders.jsx";
import Tiles from "./components/Tiles.jsx";

const DEFAULT_ASSUMPTIONS = { trend: null, threshold: 2000, premium_change: 0, at_risk_lr: 0.85, seasonality: true };
const NO_FILTERS = { species_mix: [], region: [], deductible: [], tenure: [], size: [] };

// Wait until the user stops dragging before calling the API.
function useDebounced(value, ms = 250) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export default function App() {
  const [assumptions, setAssumptions] = useState(DEFAULT_ASSUMPTIONS);
  const [filters, setFilters] = useState(NO_FILTERS);
  const [by, setBy] = useState("region");
  const [atRiskOnly, setAtRiskOnly] = useState(false);
  const [selected, setSelected] = useState(null);

  const [meta, setMeta] = useState(null);
  const [segment, setSegment] = useState(null);
  const [groups, setGroups] = useState(null);
  const [breakdown, setBreakdown] = useState(null);
  const [backtest, setBacktest] = useState(null);
  const [monitoring, setMonitoring] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  const a = useDebounced(assumptions);
  const query = useMemo(() => buildQuery(a, filters), [a, filters]);
  const assumptionQuery = useMemo(() => buildQuery(a), [a]);

  useEffect(() => {
    getJSON("/meta").then(setMeta).catch((e) => setError(e.message));
    getJSON("/backtest").then(setBacktest).catch(() => setBacktest(null));
    getJSON("/monitoring").then(setMonitoring).catch(() => setMonitoring(null));
  }, []);

  useEffect(() => {
    let live = true;
    setLoading(true);
    Promise.all([
      getJSON("/segments", query),
      getJSON("/groups", buildQuery(a, filters, { at_risk_only: atRiskOnly })),
      getJSON("/segments/breakdown", buildQuery(a, filters, { by })),
    ])
      .then(([s, g, b]) => {
        if (!live) return;
        setSegment(s); setGroups(g); setBreakdown(b); setError(null);
      })
      .catch((e) => live && setError(e.message))
      .finally(() => live && setLoading(false));
    return () => { live = false; };
  }, [query, a, filters, atRiskOnly, by]);

  const estimatedTrend = meta?.context?.estimated_trend;
  const trendUsed = segment?.trend_used ?? estimatedTrend;

  return (
    <>
      <header className="app-header">
        <div>
          <h1>Pet insurance loss-ratio forecaster</h1>
          <div className="sub">
            Synthetic data · {meta ? `${meta.groups_active} active groups · data through ${monthLabel(meta.as_of)}` : "loading…"}
            {trendUsed !== undefined && ` · trend ${pct(trendUsed)} (${segment?.trend_source ?? "estimated"})`}
          </div>
        </div>
        <div className="header-actions">
          <a className="btn primary" href={exportUrl(a, filters)} download>
            <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M7 1v8M3.5 5.5 7 9l3.5-3.5M2 12h10" stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
            Export CSV
          </a>
          <a className="btn" href="/api/docs" target="_blank" rel="noreferrer">API docs</a>
        </div>
      </header>

      <div className="layout">
        <aside className="sidebar">
          <Sliders value={assumptions} onChange={setAssumptions} estimatedTrend={estimatedTrend}
            onReset={() => setAssumptions(DEFAULT_ASSUMPTIONS)} />
          <Filters options={meta?.filters} value={filters} onChange={setFilters} />
        </aside>

        <main className={`main ${loading ? "loading" : ""}`}>
          {error && <div className="error" role="alert">Something went wrong: {error}. Is the API running and has the pipeline run?</div>}
          <Tiles headline={segment?.headline} atRiskLr={a.at_risk_lr} />
          <LossRatioChart title="Loss ratio: history and 12-month projection"
            hint="Claims ÷ premium each month. Recent months are grossed up for claims not yet paid."
            history={segment?.history ?? []} projection={segment?.projection ?? []} atRiskLr={a.at_risk_lr} />
          {selected && (
            <GroupDetail groupId={selected} query={assumptionQuery} atRiskLr={a.at_risk_lr}
              trend={trendUsed} onClose={() => setSelected(null)} />
          )}
          <div className="two-col">
            <Breakdown data={breakdown} by={by} setBy={setBy} atRiskLr={a.at_risk_lr} />
            <Backtest data={backtest} />
          </div>
          <Monitoring data={monitoring} />
          {groups && (
            <GroupsTable groups={groups.groups} count={groups.count} selected={selected}
              onSelect={setSelected}
              atRiskOnly={atRiskOnly} setAtRiskOnly={setAtRiskOnly} />
          )}
        </main>
      </div>
    </>
  );
}
