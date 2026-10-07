# Learning guide

This guide walks through the project in the order it was built. Each phase says **what** we did, **why**, and **what to remember**. The key concepts and 20 interview questions are at the end.

---

## The business problem in one paragraph

A pet insurer sells cover through **partner groups** (employers, breeder clubs, ...). Each group pays a **premium** every month for each pet; the insurer pays **claims** when pets visit the vet. The **loss ratio** = claims ÷ premium. If a group's loss ratio is heading above ~85%, the insurer may need to re-price it or talk to the partner. We want to **project each group's loss ratio for the next 12 months**, and to say **how much to trust** each projection.

---

## Phase 1: Synthetic data generator (`generate.py`)

**What.** We simulate the world and then *break* it on purpose.

- 300 groups, each with a region, deductible, dog/cat mix and size (10–2,000 pets).
- Pets join and leave every month; ~8% of groups cancel.
- Claims = **frequency × severity**. Each pet has ~0.08 claims a month (cats fewer); each claim costs ~$550 on average. On top of that:
  - **Seasonality**: +12% in July, −12% in January (summer = ticks, heat, foreign objects).
  - **Trend**: vet costs grow 8% a year.
  - **Large claims**: 1.5% of claims are ~$12k surgeries.
  - **A hidden shock**: the West region gets 15% more expensive in the last 4 months, so monitoring has something to find.
- Claims are paid **after** the vet visit: most within a month, ~10% 2–7 months later. Anything not paid by the extract date isn't in the file.

**Planted problems** (saved in `data/raw/_planted.json` so tests can check we caught them):

| Problem | Real-world cause |
|---|---|
| Duplicate rows | A file batch re-sent |
| Pets of cancelled groups still listed | The cancellation didn't cascade to pets |
| Late-paid claims | Normal: vets bill late, claims get reviewed |
| Dates shifted back one day (1 Mar → 28 Feb) | Timezone conversion bug (UTC midnight) |
| Bad group start dates (1900, 2099, after the first bill) | Placeholder or typo values |

**Remember.** Synthetic data lets you **know the truth** (8% trend, West shock), so you can check whether your methods recover it. That's hard to do with real data.

---

## Phase 2: Pipeline and data-quality gate (`pipeline.py`, `quality.py`, `flows.py`)

**What.** Turn four messy CSVs into **one row per group per month**: pet-months, premium, claims.

The **gate** has two layers, both built with **pandera**:

1. **Raw checks**: what perfect raw data would look like. For example, `claim_id` is unique, billing dates are the 1st of the month, every pet in a cancelled group has an end date. These *will* fail on real data, and every failure becomes an **issue** in the report with a count, examples and the action taken. Each known issue has a **tolerance**: 1% duplicates is normal mess, but 50% means the extract is broken, so the gate stops.
2. **Clean checks**: what the model is allowed to use. Unique `(group, month)`, premium > 0, and a **reconciliation** check: *pets billed = pets enrolled* every month. If we forgot to end cancelled groups' pets, this fails.

`lazy=True` makes pandera **collect every failure** instead of stopping at the first one. The report is always written to `data/out/quality_report.json`, even when the gate fails.

**Fixes, in order:**

1. Snap dates to the nearest 1st of the month, *then* drop exact duplicates (a duplicated row whose copy was shifted only becomes an exact duplicate after the snap).
2. End pets of cancelled groups at the cancel date.
3. Replace bad start dates with the first month we actually see the group.
4. Book each claim to the month of the **vet visit** (incurred basis), not the payment month.
5. **Completion factors**: from older, fully paid months we learn that a brand-new month typically shows only ~35% of its final claims, one month later ~80%, and so on. We divide recent months by that factor ("gross them up"). This is a simple version of the **chain-ladder** method used to estimate **IBNR** (incurred but not reported).

**Prefect** wraps it all in a flow: each step is a task with logs and retries (but **not** for the gate, because bad data won't fix itself on retry). `python -m petlr.flows --serve` runs it daily at 06:00. Cron schedules need a real Prefect server; Docker Compose provides one.

**Remember.** "Fail loudly" means: never silently drop or "fix" something you don't understand. Unknown problem → fatal. Known problem → fixed **and** reported.

---

## Phase 3: Baseline projection (`projection.py`)

For each group still active in the latest month:

1. **Seasonality index**: average cost per pet per month (PPPM) by calendar month, after removing trend; scaled so the 12 values average 1. July ≈ 1.10, January ≈ 0.91.
2. **Trend**: compare each month with **the same month last year** (year-over-year) and take the geometric mean of the ratios. Because July is compared with July, seasonality cancels out exactly.
3. **Normalize experience** (last 12 months): divide each month's claims by its seasonality index and multiply by `(1 + trend)^(months ago / 12)`. Every month is now at *today's* cost level with no seasonal bump.
4. **Cap large claims** at $5,000 per claim. Own PPPM uses capped claims only.
5. **Segment benchmark**: the same calculation for all groups with the same species mix × deductible.
6. **Credibility**: `Z = min(1, √(pet_months / 2,000))`.
7. **Blend**: `Z × own + (1 − Z) × benchmark`.
8. **Large-claim load**: portfolio excess ÷ portfolio capped (≈18%) is added back: `× (1 + load)`.
9. **Roll forward**: for month *h* ahead, `PPPM × (1 + trend)^(h/12) × seasonality[month] × pets`.
10. **Premium**: latest premium per pet × (1 + your rate change) × pets. Loss ratio = claims ÷ premium. Above the at-risk line (85%) → flagged.

**Worked example (from `test_projection.py`).** Group A: 1,200 pet-months, own PPPM $50. Group B: 3,600 pet-months, own PPPM $30. Segment benchmark = ($60k + $108k) ÷ 4,800 = **$35**. With threshold 4,800: Z_A = √(1,200/4,800) = **0.5**, so A's projected PPPM = 0.5 × 50 + 0.5 × 35 = **$42.50**. A had a bad year; we believe half of it.

**Remember.** Every number on the website shows its Z, so a user can tell a confident projection from a guess.

---

## Phase 4: Back-test (`backtest.py`)

**What.** Pretend it's six months ago. Rebuild history using **only claims paid by then** (with completion factors estimated then), project the next 6 months, and compare with what actually happened.

**Why the "as known then" detail matters.** If you use today's data for the past, late payments that arrived *after* the cutoff leak into the history. Your back-test then looks better than reality. This is called **look-ahead bias** or **leakage**. A test (`test_no_future_leakage`) guards against it.

**Three models compared:**

| Model | Group error (WAPE) | Small groups (Z < 0.3) |
|---|---|---|
| Credibility blend | **21.4%** | **41%** |
| Own experience only (Z = 1) | 21.8% | 69% |
| Benchmark only (Z = 0) | 26.6% | 43% |

- **WAPE** (weighted absolute percentage error) = Σ|actual − expected| ÷ Σ actual. Unlike plain MAPE it doesn't blow up for tiny groups.
- **Bias** = Σ expected ÷ Σ actual − 1 (negative = we projected too low).

**What the back-test taught us (and what changed):**

1. With the original threshold (6,000 pet-months) credibility barely beat "own only". Scanning thresholds showed the error is lowest around **2,000**, so that became the default. This is *calibrating an assumption with data*.
2. The first trend method (a straight-line fit through 18 months) gave 4%, because the data didn't start in January and the fit mistook part of the seasonal swing for trend. Year-over-year fixes that.
3. A **−7.6% portfolio bias** remains. At the cutoff only 6 same-month pairs existed, a few cheap months pulled the trend estimate to 3.9%, and the West shock started after the cutoff. Lesson: trend estimated from short history is noisy, which is why actuaries usually set trend as a **judgment assumption** (our slider) informed by industry data.

**Remember.** A back-test isn't just a score. Compare against simple baselines and look *where* the model wins (here: small groups).

---

## Phase 5: API (`api.py`)

FastAPI turns the model into URLs. Assumptions are **query parameters** (`?trend=0.08&threshold=3000&premium_change=0.05`), so the website can recalculate live. FastAPI validates them: `threshold=-5` returns a 422 error before any model code runs.

- `/api/segments`: the segment view. Filter by species mix, region, deductible, tenure and size; get headline numbers, history and projection.
- `/api/groups`, `/api/groups/{id}`: the list and the single-group view (with own, benchmark, Z and blend).
- `/api/export.csv`, `/api/backtest`, `/api/quality`, `/api/monitoring`.

Projections are **cached per assumption set** (`lru_cache`; the frozen dataclass makes `Assumptions` hashable), and the cache clears when the pipeline writes new data.

---

## Phase 6: Website (`frontend/`)

- **Sliders** are **debounced** (250 ms) so dragging doesn't fire 50 API calls.
- **Headline tiles**: projected LR, claims, premium, groups at risk, pooled credibility. Each tile shows `avg Z` and "% from own experience".
- **Chart**: solid line = actual, dashed = projected, grey line = at-risk threshold. Tooltip shows Z; a **table view** gives the same numbers without hovering (accessibility).
- **Groups table**: sortable, with a Z meter and an at-risk flag that uses an **icon + word**, never colour alone (colour-blind safe).
- **Group detail**: the projection built step by step, which is the best way to explain credibility to a non-actuary.
- **CSV export** downloads exactly what you're looking at, including the assumptions used.

---

## Phase 7: Monitoring, Docker, CI

**Monitoring** (`monitoring.py`) answers three questions:

1. **Is the data fresh?** Latest month vs the last complete calendar month; how long since the pipeline last ran.
2. **Has the data drifted?** **PSI** (Population Stability Index) compares distributions: group cost per pet, claim frequency, premium per pet, dog share and claim size. We compare the last 6 months with **the same 6 months a year earlier** so summer-vs-winter doesn't look like drift. Rule of thumb: PSI < 0.1 stable, 0.1–0.25 watch, > 0.25 investigate. **Segment drift** compares each region's year-over-year cost growth with all *other* regions, using capped claims. It flags the planted West shock: **+24% vs +12%**.
3. **Is the model still right?** Actual ÷ expected (A/E) from the back-test and from saved projection snapshots once their months arrive. Outside ±10% → warning.

**Docker Compose** starts five services: Prefect server, a one-shot pipeline run, the daily scheduler, the API and nginx serving the website. `depends_on` with `service_completed_successfully` ensures the API only starts after data exists.

**CI** (GitHub Actions) on every push: ruff lint, pytest, a pipeline smoke test, frontend tests and build, a **gitleaks** secret scan of the whole history, and a Compose build + smoke test.

---

## Key concepts

### Credibility
How much weight to put on a group's own history versus a broader average. Small groups have noisy history (one sick dog doubles their claims), so we shrink them toward the benchmark. Formula used: `Z = min(1, √(n / n_full))`. This is **limited-fluctuation** ("classical") credibility. The other famous version, **Bühlmann**, uses `Z = n / (n + k)` where `k` = (average within-group variance) ÷ (variance between groups). Both say the same thing: more data → more trust.

### Seasonality
A repeating pattern within the year. We estimate one multiplier per calendar month. Ignoring it would make a group that joined in summer look expensive.

### Trend
Steady growth in costs (vet prices, more treatments). Applied twice: to bring old experience **up** to today's level, and to project **forward**.

### Back-testing
Train on the past, predict a period you hid, measure the error. Use only data known at the cutoff, and compare against simple baselines.

### Data-quality gates
Automated, version-controlled rules that sit between raw data and the model. They **report everything**, **fix known issues transparently**, and **stop** on unknown ones.

### Drift
The world changing under the model. **Data drift**: input distributions move (PSI). **Concept drift**: the relationship changes, so the model's errors move (A/E). Monitoring catches both before users do.

---

## 20 interview questions and answers

**1. What is a loss ratio and why does an insurer care?**
Claims paid ÷ premium earned. It's the core profitability measure: the remaining ~30% has to cover expenses, commissions and profit. A group projected above ~85% is likely to lose money.

**2. Why not just use each group's own history to project it?**
Small groups have tiny samples. A group of 15 pets with one $12k cancer claim looks terrible, but that tells you little about next year. The back-test shows it: for low-credibility groups, "own only" had 69% error vs 41% for the blend.

**3. Explain credibility in one sentence.**
A weight Z between 0 and 1 saying how much to believe a group's own experience versus a benchmark: `Z × own + (1 − Z) × benchmark`.

**4. Why a square root in `Z = √(n / threshold)`?**
The noise in an average shrinks with √n, so confidence grows like √n, not n. Doubling a group's size doesn't double how much you trust it.

**5. How did you choose the full-credibility threshold?**
Empirically: I ran the back-test at thresholds from 250 to 12,000 pet-months and picked the one with the lowest error (2,000). Too high trusts groups too little; too low trusts noisy groups too much.

**6. What's the difference between limited-fluctuation and Bühlmann credibility?**
Limited-fluctuation sets a "full credibility" volume and uses `√(n/N)` below it. Bühlmann derives `Z = n/(n+k)` from the data's within-group and between-group variance, so it's more theoretically grounded but needs variance estimates.

**7. How did you remove seasonality?**
Built a monthly index from portfolio cost per pet with the trend removed, normalized to average 1, and divided each month's claims by its index before averaging. When projecting, multiply it back in.

**8. Why estimate trend year-over-year rather than with a regression?**
Comparing July with July cancels seasonality exactly. A straight-line fit on 18 seasonal months that don't start in January confuses part of the seasonal swing with trend; it gave 4% when the truth was 8%.

**9. Why cap large claims?**
One $20k claim can dominate a small group's year and would wrongly make it look high-risk. We project the capped part per group and spread large claims across everyone as a load (≈18%), since they're largely random.

**10. What are completion factors / IBNR?**
Claims for recent months are still arriving. From older months we learn what share is usually paid by each age (e.g. 35% in month 0, 80% by month 1) and divide recent months by that share. It's a simplified chain-ladder estimate of **incurred but not reported** claims.

**11. What is look-ahead bias in a back-test, and how did you avoid it?**
Using information that wasn't available at the time. I rebuilt history using only claims **paid** by the cutoff and recomputed completion factors as of then; a unit test checks that a late-paid claim is invisible before its payment date.

**12. Which error metrics did you use and why?**
WAPE (Σ|A−E|/ΣA) for groups, because it's weighted by size and doesn't explode on tiny groups like MAPE; bias (ΣE/ΣA − 1) to see systematic over- or under-projection; and premium-weighted loss-ratio error, because that's the business number.

**13. Your back-test shows −7.6% bias. Is the model broken?**
No. Only six same-month pairs existed at the cutoff, so the trend estimate was noisy (3.9% vs 8% true), and a regional cost shock began after the cutoff. In practice trend is a judgment assumption, and monitoring exists to catch shocks; ours flags the West region.

**14. What does your data-quality gate do differently from just cleaning data?**
It's explicit and tested: it declares what valid data looks like, reports every violation (pandera `lazy=True`), fixes only *known* problems with documented actions and tolerances, and raises an error on anything unknown, writing a full report either way.

**15. Give an example of a reconciliation check and why it matters.**
"Pets billed = pets enrolled" for every group-month. If cancelled groups' pets were still listed, exposure would exceed billing and claims per pet would be understated. The check makes that bug impossible to miss.

**16. What is PSI and how do you read it?**
Population Stability Index: bin a reference sample by its deciles, then sum `(cur% − ref%) × ln(cur% / ref%)`. Under 0.1 is stable, 0.1–0.25 worth watching, over 0.25 a significant shift.

**17. Why compare against the same months last year for drift?**
Otherwise ordinary seasonality (summer is expensive) shows up as drift every year. Matching calendar months leaves only trend and genuine change.

**18. How would you know the model has gone wrong in production?**
Track actual ÷ expected for each month as actuals arrive (from saved projection snapshots), by portfolio and segment, with alert thresholds (±10% warn, ±20% fail), plus the drift and freshness checks.

**19. Why is Prefect useful here instead of a cron job running a script?**
Tasks get retries, logs, run history, a UI and alerting for free; failures show which step broke; and the same flow runs locally, on a schedule or in Docker without changes.

**20. How would you improve this model?**
Add rating factors (region, breed, pet age) to the benchmark with a GLM; use Bühlmann credibility with estimated variances; model frequency and severity separately; set trend from longer history or industry indices; project premium with known renewal increases; and add prediction intervals so users see uncertainty, not just Z.
