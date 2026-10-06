const LABELS = {
  species_mix: "Species mix",
  region: "Region",
  deductible: "Deductible",
  tenure: "Tenure",
  size: "Group size",
};
const PRETTY = {
  dog_heavy: "Dog-heavy", mixed: "Mixed", cat_heavy: "Cat-heavy",
  new: "New (<1y)", established: "1–3 years", mature: "3+ years",
  small: "<50 pets", medium: "50–249", large: "250–999", jumbo: "1,000+",
};

export function pretty(value) {
  if (typeof value === "number") return `$${value}`;
  return PRETTY[value] ?? value;
}

export default function Filters({ options, value, onChange }) {
  if (!options) return null;
  const toggle = (key, v) => {
    const current = value[key] || [];
    const next = current.includes(v) ? current.filter((x) => x !== v) : [...current, v];
    onChange({ ...value, [key]: next });
  };
  const active = Object.values(value).some((v) => v.length);
  return (
    <section className="card" aria-labelledby="filters-title">
      <div className="card-head">
        <h2 id="filters-title">Segment filters</h2>
        {active && (
          <button className="btn link" onClick={() => onChange(Object.fromEntries(Object.keys(value).map((k) => [k, []])))}>
            Clear
          </button>
        )}
      </div>
      <p className="hint">No selection = everything. Pick several to combine.</p>
      {Object.entries(LABELS).map(([key, label]) => (
        <div className="filter-group" key={key}>
          <div className="name">{label}</div>
          <div className="chips">
            {options[key].map((v) => (
              <button key={v} className="chip" aria-pressed={(value[key] || []).includes(v)}
                onClick={() => toggle(key, v)}>
                {pretty(v)}
              </button>
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}
