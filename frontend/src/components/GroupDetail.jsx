import { useEffect, useRef, useState } from "react";
import { getJSON } from "../api.js";
import { money, pct } from "../format.js";
import { pretty } from "./Filters.jsx";
import LossRatioChart from "./LossRatioChart.jsx";
import { RiskFlag, ZMeter } from "./Bits.jsx";

// "Show your work" for one group: the credibility blend, step by step.
export default function GroupDetail({ groupId, query, atRiskLr, trend, onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const ref = useRef(null);
  useEffect(() => {
    ref.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [groupId]);
  useEffect(() => {
    let live = true;
    getJSON(`/groups/${groupId}`, query)
      .then((d) => live && (setData(d), setError(null)))
      .catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [groupId, query]);

  if (error) return <div className="error" ref={ref}>Could not load {groupId}: {error}</div>;
  if (!data) return <section className="card muted" ref={ref}>Loading {groupId}…</section>;
  const s = data.summary;
  const a = data.attributes;
  return (
    <section className="card" aria-labelledby="detail-title" ref={ref}>
      <div className="card-head">
        <div>
          <h2 id="detail-title">Group {data.group_id} {s && <RiskFlag atRisk={s.at_risk} />}</h2>
          <p className="hint" style={{ margin: 0 }}>
            {a.region} · {pretty(a.species_mix)} · {pretty(a.deductible)} deductible
            {s ? ` · ${s.pets} pets · ${pretty(s.tenure_band)}` : ""}
            {data.cancel_date ? ` · cancelled ${data.cancel_date}` : ""}
          </p>
        </div>
        <button className="btn" onClick={onClose}>Close</button>
      </div>
      {!s ? (
        <p className="muted">This group is no longer active, so it has history but no projection.</p>
      ) : (
        <div className="detail-grid">
          <div className="recipe" aria-label="How the projection was built">
            <div className="line"><span>Own cost per pet per month (capped, trended)</span><span>{money(s.own_pppm)}</span></div>
            <div className="line"><span>Segment benchmark ({a.segment.replace("|", ", $")})</span><span>{money(s.bench_pppm)}</span></div>
            <div className="line"><span>Experience used</span><span>{s.pet_months_exp.toLocaleString()} pet-months</span></div>
            <div className="line"><span>Credibility</span><span><ZMeter z={s.z} showWord /></span></div>
            <div className="line">
              <span>Blend = {s.z.toFixed(2)} × own + {(1 - s.z).toFixed(2)} × benchmark</span>
              <span>{money(s.blended_pppm)}</span>
            </div>
            <div className="line"><span>+ large-claim load ({pct(data.large_claim_load)})</span><span>{money(s.expected_pppm)}</span></div>
            <div className="line"><span>× trend ({pct(trend)}/yr) × seasonality × pets</span><span>{money(s.proj_claims)} claims</span></div>
            <div className="line"><span>Projected premium</span><span>{money(s.proj_premium)}</span></div>
            <div className="line total"><span>Projected loss ratio (12 months)</span><span>{pct(s.proj_lr)}</span></div>
            <div className="line"><span>Last 12 months, actual</span><span>{pct(s.hist_lr)}</span></div>
          </div>
          <LossRatioChart title="Monthly loss ratio" history={data.history} projection={data.projection}
            atRiskLr={atRiskLr} hint={`Projected months use Z ${s.z.toFixed(2)}`} />
        </div>
      )}
    </section>
  );
}
