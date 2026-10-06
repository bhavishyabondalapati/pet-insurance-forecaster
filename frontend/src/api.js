// Talking to the FastAPI backend. All model assumptions travel as query params.

export function buildQuery(assumptions, filters = {}, extra = {}) {
  const p = new URLSearchParams();
  if (assumptions.trend !== null && assumptions.trend !== undefined) {
    p.set("trend", String(assumptions.trend));
  }
  p.set("threshold", String(assumptions.threshold));
  p.set("premium_change", String(assumptions.premium_change));
  p.set("at_risk_lr", String(assumptions.at_risk_lr));
  p.set("seasonality", String(assumptions.seasonality));
  for (const [key, values] of Object.entries(filters)) {
    for (const v of values || []) p.append(key, String(v));
  }
  for (const [key, v] of Object.entries(extra)) {
    if (v !== undefined && v !== null) p.set(key, String(v));
  }
  return p.toString();
}

export async function getJSON(path, query = "") {
  const res = await fetch(`/api${path}${query ? `?${query}` : ""}`);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new Error(`${res.status}: ${typeof detail === "string" ? detail : JSON.stringify(detail)}`);
  }
  return res.json();
}

export function exportUrl(assumptions, filters) {
  return `/api/export.csv?${buildQuery(assumptions, filters)}`;
}
