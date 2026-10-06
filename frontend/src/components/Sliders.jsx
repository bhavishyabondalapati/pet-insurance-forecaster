import { pct } from "../format.js";

function Slider({ id, label, value, min, max, step, format, help, onChange, disabled }) {
  return (
    <div className="slider">
      <div className="slider-top">
        <label htmlFor={id}>{label}</label>
        <span className="value">{format(value)}</span>
      </div>
      <input id={id} type="range" min={min} max={max} step={step} value={value} disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))} />
      {help && <div className="help">{help}</div>}
    </div>
  );
}

export default function Sliders({ value, onChange, estimatedTrend, onReset }) {
  const set = (key) => (v) => onChange({ ...value, [key]: v });
  const manualTrend = value.trend !== null;
  return (
    <section className="card" aria-labelledby="assumptions-title">
      <div className="card-head">
        <h2 id="assumptions-title">Assumptions</h2>
        <button className="btn link" onClick={onReset}>Reset</button>
      </div>
      <p className="hint">Every number on the page is recalculated when you move a slider.</p>

      <Slider id="trend" label="Vet-cost trend (per year)"
        value={manualTrend ? value.trend : estimatedTrend ?? 0}
        min={-0.05} max={0.25} step={0.005} format={(v) => pct(v, 1)}
        disabled={!manualTrend} onChange={set("trend")}
        help={manualTrend ? "Your assumption" : "Estimated from the data (year-over-year)"} />
      <label className="check">
        <input type="checkbox" checked={!manualTrend}
          onChange={(e) => onChange({ ...value, trend: e.target.checked ? null : Number((estimatedTrend ?? 0.08).toFixed(3)) })} />
        Use the trend estimated from data
      </label>
      <div style={{ height: 14 }} />

      <Slider id="threshold" label="Full-credibility threshold" value={value.threshold}
        min={250} max={12000} step={250} format={(v) => `${v.toLocaleString()} pet-months`}
        onChange={set("threshold")}
        help="Z = min(1, √(pet-months ÷ threshold)). Higher = trust groups less." />
      <Slider id="premium" label="Premium rate change" value={value.premium_change}
        min={-0.1} max={0.25} step={0.01} format={(v) => `${v >= 0 ? "+" : ""}${pct(v, 0)}`}
        onChange={set("premium_change")} help="Applied to every group's projected premium." />
      <Slider id="atrisk" label="At-risk loss ratio" value={value.at_risk_lr}
        min={0.5} max={1.5} step={0.01} format={(v) => pct(v, 0)}
        onChange={set("at_risk_lr")} help="Groups projected above this are flagged." />
      <label className="check">
        <input type="checkbox" checked={value.seasonality}
          onChange={(e) => onChange({ ...value, seasonality: e.target.checked })} />
        Apply seasonality (summer peak)
      </label>
    </section>
  );
}
