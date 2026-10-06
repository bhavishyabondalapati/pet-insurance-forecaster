import { credibilityLabel } from "../format.js";

// Credibility meter: a small blue bar + "Z 0.62 (medium)". Shown next to every number.
export function ZMeter({ z, showWord = false }) {
  if (z === null || z === undefined) return <span className="muted">–</span>;
  return (
    <span className="zmeter" title={`Credibility Z = ${z.toFixed(2)}: ${Math.round(z * 100)}% own experience, ${Math.round((1 - z) * 100)}% segment benchmark`}>
      <span className="track" aria-hidden="true">
        <span className="fill" style={{ width: `${Math.max(2, z * 100)}%`, display: "block" }} />
      </span>
      <span className="txt">
        Z {z.toFixed(2)}
        {showWord ? ` · ${credibilityLabel(z)}` : ""}
      </span>
    </span>
  );
}

// Status is never color alone: icon + text label.
export function RiskFlag({ atRisk }) {
  if (!atRisk) {
    return (
      <span className="status good">
        <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
          <circle cx="6" cy="6" r="5" fill="none" stroke="currentColor" strokeWidth="1.5" />
        </svg>
        OK
      </span>
    );
  }
  return (
    <span className="status critical">
      <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
        <path d="M6 1 L11 10.5 H1 Z" fill="var(--critical)" />
        <rect x="5.4" y="4.2" width="1.2" height="3.4" fill="#fff" />
        <rect x="5.4" y="8.3" width="1.2" height="1.2" fill="#fff" />
      </svg>
      At risk
    </span>
  );
}
