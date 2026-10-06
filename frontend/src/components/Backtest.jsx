import { monthLabel, pct } from "../format.js";

const NAMES = { credibility: "Credibility blend", own_only: "Own experience only (Z=1)",
  bench_only: "Benchmark only (Z=0)" };

// How well did the model do on 6 months it was not allowed to see?
export default function Backtest({ data }) {
  if (!data) return null;
  const best = Object.entries(data.models).sort((a, b) => a[1].group.wape - b[1].group.wape)[0][0];
  return (
    <section className="card">
      <h2>Back-test: last {data.holdout_months} months hidden</h2>
      <p className="hint">
        History cut at {monthLabel(data.cutoff)} using only claims paid by then; the model projected the
        hidden months. Lower error is better.
      </p>
      <div className="table-scroll">
        <table>
          <thead><tr><th>Model</th><th className="num">Group error (WAPE)</th>
            <th className="num">Portfolio bias</th><th className="num">LR error</th></tr></thead>
          <tbody>
            {Object.entries(data.models).map(([k, m]) => (
              <tr key={k} style={{ cursor: "default" }}>
                <td>{NAMES[k]}{k === best && <span className="badge" style={{ marginLeft: 6 }}>best</span>}</td>
                <td className="num">{pct(m.group.wape)}</td>
                <td className="num">{m.portfolio.bias >= 0 ? "+" : ""}{pct(m.portfolio.bias)}</td>
                <td className="num">{pct(m.group.lr_mae)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h2 style={{ marginTop: 16 }}>Error by credibility band</h2>
      <p className="hint">Small groups (low Z) are where blending with the benchmark helps most.</p>
      <div className="table-scroll">
        <table>
          <thead><tr><th>Band</th><th className="num">Groups</th><th className="num">Blend</th>
            <th className="num">Own only</th><th className="num">Benchmark only</th></tr></thead>
          <tbody>
            {data.by_credibility_band.map((b) => (
              <tr key={b.band} style={{ cursor: "default" }}>
                <td>{b.band}</td><td className="num">{b.groups}</td>
                <td className="num">{pct(b.credibility)}</td><td className="num">{pct(b.own_only)}</td>
                <td className="num">{pct(b.bench_only)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h2 style={{ marginTop: 16 }}>Actual ÷ expected by month</h2>
      <div className="table-scroll">
        <table>
          <thead><tr><th>Month</th><th className="num">Projected LR</th><th className="num">Actual LR</th>
            <th className="num">A/E</th></tr></thead>
          <tbody>
            {data.by_month.map((m) => (
              <tr key={m.month} style={{ cursor: "default" }}>
                <td>{monthLabel(m.month)}</td><td className="num">{pct(m.proj_lr)}</td>
                <td className="num">{pct(m.actual_lr)}</td><td className="num">{m.a_to_e.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
