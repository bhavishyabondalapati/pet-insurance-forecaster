import { money, num, pct } from "../format.js";
import { ZMeter } from "./Bits.jsx";

// Headline numbers. Every tile carries the credibility behind it.
export default function Tiles({ headline, atRiskLr }) {
  if (!headline) return null;
  const h = headline;
  const lrDelta = h.proj_lr !== null && h.hist_lr !== null ? h.proj_lr - h.hist_lr : null;
  const cred = (
    <span className="cred">
      <ZMeter z={h.avg_z} /> {pct(h.own_share, 0)} from own experience
    </span>
  );
  return (
    <div className="tiles">
      <div className="tile hero" style={{ gridColumn: "span 2" }}>
        <span className="label">Projected loss ratio, next 12 months</span>
        <span className="value">{pct(h.proj_lr)}</span>
        <span className="delta">
          {lrDelta === null ? "" : `${lrDelta >= 0 ? "+" : "−"}${(Math.abs(lrDelta) * 100).toFixed(1)} pts vs last 12 months (${pct(h.hist_lr)})`}
        </span>
        {cred}
      </div>
      <div className="tile">
        <span className="label">Projected claims</span>
        <span className="value">{money(h.proj_claims)}</span>
        <span className="delta">{num(h.pets)} pets · {h.groups} groups</span>
        {cred}
      </div>
      <div className="tile">
        <span className="label">Projected premium</span>
        <span className="value">{money(h.proj_premium)}</span>
        <span className="delta">at current rates ± your change</span>
        <span className="cred">Premium is known, so no credibility needed</span>
      </div>
      <div className="tile">
        <span className="label">Groups at risk</span>
        <span className="value">{h.at_risk_groups}</span>
        <span className="delta">projected above {pct(atRiskLr, 0)}</span>
        {cred}
      </div>
      <div className="tile">
        <span className="label">Pooled credibility</span>
        <span className="value">{h.pooled_z.toFixed(2)}</span>
        <span className="delta">{num(h.pet_months)} pet-months, if treated as one group</span>
        <span className="cred"><ZMeter z={h.pooled_z} showWord /></span>
      </div>
    </div>
  );
}
