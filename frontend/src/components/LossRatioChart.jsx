import { useState } from "react";
import {
  CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { money, monthLabel, num, pct } from "../format.js";

// Monthly loss ratio: actual history (solid) running into the projection (dashed).
// Credibility (Z) for projected months appears in the tooltip and in the table view.
export function buildRows(history, projection) {
  const rows = history.map((h) => ({
    month: h.month, actual: h.loss_ratio, claims: h.claims, premium: h.premium,
    pets: h.pet_months, developing: h.completion_factor !== undefined && h.completion_factor < 1,
    kind: "actual",
  }));
  if (rows.length && projection.length) rows[rows.length - 1].projected = rows[rows.length - 1].actual;
  for (const p of projection) {
    rows.push({
      month: p.month, projected: p.loss_ratio, claims: p.claims, premium: p.premium,
      pets: p.pets, z: p.avg_z ?? p.z, kind: "projected",
    });
  }
  return rows;
}

function ChartTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const r = payload[0].payload;
  const lr = r.kind === "actual" ? r.actual : r.projected;
  return (
    <div className="tooltip">
      <div className="t-title">{monthLabel(r.month)} · {r.kind === "actual" ? "actual" : "projected"}</div>
      <div className="row"><span>Loss ratio</span><span>{pct(lr)}</span></div>
      <div className="row"><span>Claims</span><span>{money(r.claims)}</span></div>
      <div className="row"><span>Premium</span><span>{money(r.premium)}</span></div>
      <div className="row"><span>Pets</span><span>{num(r.pets)}</span></div>
      {r.kind === "projected" && <div className="row"><span>Credibility</span><span>Z {r.z?.toFixed(2)}</span></div>}
      {r.developing && <div className="row"><span>Note</span><span>grossed up for late claims</span></div>}
    </div>
  );
}

export default function LossRatioChart({ history = [], projection = [], atRiskLr, title, hint }) {
  const [showTable, setShowTable] = useState(false);
  const rows = buildRows(history, projection);
  const values = rows.flatMap((r) => [r.actual, r.projected]).filter((v) => v !== undefined);
  // Clean ticks every 25 points (every 50 when the range is large).
  const step = Math.max(atRiskLr ?? 0, ...values) > 1.5 ? 0.5 : 0.25;
  const top = Math.ceil((Math.max(atRiskLr ?? 0, ...values) * 1.05) / step) * step;
  const ticks = Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step);
  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>{title}</h2>
          {hint && <p className="hint" style={{ margin: 0 }}>{hint}</p>}
        </div>
        <button className="btn" onClick={() => setShowTable((s) => !s)} aria-expanded={showTable}>
          {showTable ? "Show chart" : "Show table"}
        </button>
      </div>
      <div className="legend" aria-hidden={showTable}>
        <span className="key">
          <svg width="22" height="8"><line x1="0" y1="4" x2="22" y2="4" stroke="var(--series-1)" strokeWidth="2" /></svg>
          Actual
        </span>
        <span className="key">
          <svg width="22" height="8"><line x1="0" y1="4" x2="22" y2="4" stroke="var(--series-1)" strokeWidth="2" strokeDasharray="5 3" /></svg>
          Projected
        </span>
        <span className="key">
          <svg width="22" height="8"><line x1="0" y1="4" x2="22" y2="4" stroke="var(--ref-line)" strokeWidth="1" /></svg>
          At-risk line ({pct(atRiskLr, 0)})
        </span>
      </div>
      {showTable ? (
        <div className="table-scroll" style={{ maxHeight: 320 }}>
          <table>
            <thead><tr><th>Month</th><th>Type</th><th className="num">Loss ratio</th><th className="num">Claims</th>
              <th className="num">Premium</th><th className="num">Pets</th><th className="num">Z</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.month + r.kind} style={{ cursor: "default" }}>
                  <td>{monthLabel(r.month)}</td><td>{r.kind}</td>
                  <td className="num">{pct(r.kind === "actual" ? r.actual : r.projected)}</td>
                  <td className="num">{money(r.claims)}</td><td className="num">{money(r.premium)}</td>
                  <td className="num">{num(r.pets)}</td>
                  <td className="num">{r.kind === "projected" ? r.z?.toFixed(2) : "actual"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="chart-wrap">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={rows} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
              <CartesianGrid vertical={false} stroke="var(--grid)" />
              <XAxis dataKey="month" tickFormatter={monthLabel} tick={{ fill: "var(--text-muted)", fontSize: 12 }}
                axisLine={{ stroke: "var(--grid)" }} tickLine={false} minTickGap={24} />
              <YAxis domain={[0, top]} ticks={ticks} tickFormatter={(v) => pct(v, 0)} width={48}
                tick={{ fill: "var(--text-muted)", fontSize: 12 }} axisLine={false} tickLine={false} />
              <Tooltip content={<ChartTooltip />} cursor={{ stroke: "var(--ref-line)", strokeWidth: 1 }} />
              <ReferenceLine y={atRiskLr} stroke="var(--ref-line)" strokeWidth={1} />
              <Line type="linear" dataKey="actual" stroke="var(--series-1)" strokeWidth={2} dot={false}
                activeDot={{ r: 4, stroke: "var(--surface-1)", strokeWidth: 2 }} isAnimationActive={false} connectNulls={false} />
              <Line type="linear" dataKey="projected" stroke="var(--series-1)" strokeWidth={2} strokeDasharray="6 4"
                dot={false} activeDot={{ r: 4, stroke: "var(--surface-1)", strokeWidth: 2 }} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  );
}
