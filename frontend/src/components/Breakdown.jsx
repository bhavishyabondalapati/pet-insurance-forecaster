import { pct } from "../format.js";
import { pretty } from "./Filters.jsx";
import { ZMeter } from "./Bits.jsx";

const OPTIONS = { region: "Region", species_mix: "Species mix", deductible: "Deductible",
  size: "Group size", tenure: "Tenure" };

// One bar per segment value: projected LR, with the at-risk line drawn across.
export default function Breakdown({ data, by, setBy, atRiskLr }) {
  const rows = data?.rows ?? [];
  const max = Math.max(atRiskLr, ...rows.map((r) => r.proj_lr ?? 0)) * 1.05;
  return (
    <section className="card">
      <div className="card-head">
        <h2>Projected loss ratio by{" "}
          <select className="inline" value={by} onChange={(e) => setBy(e.target.value)} aria-label="Break down by">
            {Object.entries(OPTIONS).map(([k, v]) => <option key={k} value={k}>{v.toLowerCase()}</option>)}
          </select>
        </h2>
        <span className="legend" style={{ margin: 0 }}>
          <span className="key"><svg width="2" height="12"><rect width="1" height="12" fill="var(--ref-line)" /></svg>
            at-risk {pct(atRiskLr, 0)}</span>
        </span>
      </div>
      <div className="bars">
        {rows.map((r) => (
          <div className="bar-row" key={r.value}>
            <span className="cat">{pretty(r.value)}</span>
            <div className="bar-track" title={`${pretty(r.value)}: ${pct(r.proj_lr)} projected, ${r.groups} groups`}>
              <div className="bar-fill" style={{ width: `${((r.proj_lr ?? 0) / max) * 100}%` }} />
              <div className="bar-ref" style={{ left: `${(atRiskLr / max) * 100}%` }} />
            </div>
            <span className="meta">
              <strong>{pct(r.proj_lr)}</strong>
              <ZMeter z={r.avg_z} />
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
