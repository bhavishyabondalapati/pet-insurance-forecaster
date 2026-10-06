import { describe, expect, it } from "vitest";
import { buildQuery } from "./api.js";
import { credibilityLabel, money, monthLabel, pct } from "./format.js";

describe("format", () => {
  it("formats percentages and money", () => {
    expect(pct(0.7345)).toBe("73.5%");
    expect(pct(null)).toBe("–");
    expect(money(4_200_000)).toBe("$4.2M");
    expect(money(12_900)).toBe("$12.9K");
  });
  it("labels credibility", () => {
    expect(credibilityLabel(0.9)).toBe("high");
    expect(credibilityLabel(0.5)).toBe("medium");
    expect(credibilityLabel(0.1)).toBe("low");
  });
  it("formats months", () => {
    expect(monthLabel("2026-09-01")).toBe("Sep 26");
  });
});

describe("buildQuery", () => {
  const a = { trend: null, threshold: 2000, premium_change: 0, at_risk_lr: 0.85, seasonality: true };
  it("omits trend when estimated and repeats multi-value filters", () => {
    const q = new URLSearchParams(buildQuery(a, { region: ["West", "Midwest"] }));
    expect(q.has("trend")).toBe(false);
    expect(q.getAll("region")).toEqual(["West", "Midwest"]);
    expect(q.get("threshold")).toBe("2000");
  });
  it("sends a manual trend", () => {
    expect(buildQuery({ ...a, trend: 0.08 })).toContain("trend=0.08");
  });
});
