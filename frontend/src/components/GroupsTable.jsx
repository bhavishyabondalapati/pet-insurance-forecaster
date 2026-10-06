import { useState } from "react";
import { money, num, pct } from "../format.js";
import { pretty } from "./Filters.jsx";
import { RiskFlag, ZMeter } from "./Bits.jsx";

const COLUMNS = [
  { key: "group_id", label: "Group" },
  { key: "region", label: "Region" },
  { key: "species_mix", label: "Mix", fmt: pretty },
  { key: "deductible", label: "Ded.", fmt: pretty },
  { key: "pets", label: "Pets", num: true, fmt: num },
  { key: "z", label: "Credibility", num: true },
  { key: "hist_lr", label: "Last 12m LR", num: true, fmt: (v) => pct(v) },
  { key: "proj_lr", label: "Projected LR", num: true, fmt: (v) => pct(v) },
  { key: "proj_claims", label: "Proj. claims", num: true, fmt: money },
  { key: "at_risk", label: "Status" },
];

export default function GroupsTable({ groups, count, selected, onSelect, atRiskOnly, setAtRiskOnly }) {
  const [sort, setSort] = useState({ key: "proj_lr", desc: true });
  const sorted = [...groups].sort((a, b) => {
    const x = a[sort.key], y = b[sort.key];
    const c = typeof x === "string" ? x.localeCompare(y) : (x ?? -Infinity) - (y ?? -Infinity);
    return sort.desc ? -c : c;
  });
  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>Groups ({count})</h2>
          <p className="hint" style={{ margin: 0 }}>Click a row to see how its projection was built.</p>
        </div>
        <label className="check" style={{ marginTop: 0 }}>
          <input type="checkbox" checked={atRiskOnly} onChange={(e) => setAtRiskOnly(e.target.checked)} />
          At-risk only
        </label>
      </div>
      <div className="table-scroll" style={{ maxHeight: 460 }}>
        <table>
          <thead>
            <tr>
              {COLUMNS.map((c) => (
                <th key={c.key} className={c.num ? "num" : ""}
                  aria-sort={sort.key === c.key ? (sort.desc ? "descending" : "ascending") : "none"}>
                  <button onClick={() => setSort({ key: c.key, desc: sort.key === c.key ? !sort.desc : true })}>
                    {c.label}{sort.key === c.key ? (sort.desc ? " ↓" : " ↑") : ""}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((g) => (
              <tr key={g.group_id} className={selected === g.group_id ? "selected" : ""}
                onClick={() => onSelect(g.group_id)} tabIndex={0}
                onKeyDown={(e) => e.key === "Enter" && onSelect(g.group_id)}>
                {COLUMNS.map((c) => (
                  <td key={c.key} className={c.num ? "num" : ""}>
                    {c.key === "z" ? <ZMeter z={g.z} />
                      : c.key === "at_risk" ? <RiskFlag atRisk={g.at_risk} />
                      : c.fmt ? c.fmt(g[c.key]) : g[c.key]}
                  </td>
                ))}
              </tr>
            ))}
            {!sorted.length && <tr><td colSpan={COLUMNS.length} className="muted">No groups match.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}
