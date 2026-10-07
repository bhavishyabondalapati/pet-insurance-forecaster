const SECTION_NAMES = { freshness: "Freshness", drift: "Drift", projection_vs_actual: "Projection vs actual" };

function StatusIcon({ status }) {
  if (status === "ok") {
    return (
      <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
        <circle cx="7" cy="7" r="6" fill="var(--good)" />
        <path d="M4 7.2 6.1 9.2 10 5" stroke="#fff" strokeWidth="1.6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  const color = status === "fail" ? "var(--critical)" : "var(--warning)";
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
      <path d="M7 1 L13 12.5 H1 Z" fill={color} />
      <rect x="6.3" y="5" width="1.4" height="4" fill={status === "fail" ? "#fff" : "#000"} />
      <rect x="6.3" y="10" width="1.4" height="1.4" fill={status === "fail" ? "#fff" : "#000"} />
    </svg>
  );
}

const WORD = { ok: "OK", warn: "Warning", fail: "Failing" };

// Every check shows icon + word + message, so status never relies on colour alone.
export default function Monitoring({ data }) {
  if (!data) return null;
  return (
    <section className="card">
      <div className="card-head">
        <h2>Monitoring</h2>
        <span className="status" style={{ gap: 6 }}>
          <StatusIcon status={data.overall} /> Overall: {WORD[data.overall]}
          <span className="muted" style={{ fontWeight: 400 }}>
            · {data.counts.ok} ok, {data.counts.warn} warning, {data.counts.fail} failing
          </span>
        </span>
      </div>
      {Object.entries(data.sections).map(([section, checks]) => (
        <div key={section} style={{ marginTop: 12 }}>
          <div className="name" style={{ fontWeight: 500, marginBottom: 6 }}>{SECTION_NAMES[section]}</div>
          <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 6 }}>
            {checks.map((c) => (
              <li key={c.name} style={{ display: "grid", gridTemplateColumns: "92px minmax(0,1fr)", gap: 10, fontSize: 13 }}>
                <span className="status" style={{ gap: 6 }}><StatusIcon status={c.status} />{WORD[c.status]}</span>
                <span>{c.message}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}
