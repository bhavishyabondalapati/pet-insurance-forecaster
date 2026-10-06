// Small, pure formatting helpers (unit-tested in format.test.js).

export function pct(x, digits = 1) {
  if (x === null || x === undefined || Number.isNaN(x)) return "–";
  return `${(x * 100).toFixed(digits)}%`;
}

export function money(x) {
  if (x === null || x === undefined) return "–";
  const abs = Math.abs(x);
  if (abs >= 1e9) return `$${(x / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `$${(x / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `$${(x / 1e3).toFixed(1)}K`;
  return `$${x.toFixed(0)}`;
}

export function num(x) {
  if (x === null || x === undefined) return "–";
  return Math.round(x).toLocaleString("en-US");
}

// Credibility in words: how much the number leans on the group's own data.
export function credibilityLabel(z) {
  if (z === null || z === undefined) return "–";
  if (z >= 0.7) return "high";
  if (z >= 0.3) return "medium";
  return "low";
}

export function zText(z) {
  return z === null || z === undefined ? "Z –" : `Z ${z.toFixed(2)}`;
}

export function monthLabel(iso) {
  const [y, m] = iso.split("-");
  const names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${names[Number(m) - 1]} ${y.slice(2)}`;
}
