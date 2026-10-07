# Pet Insurance Loss-Ratio Forecaster

A forecaster that projects the **loss ratio** (claims ÷ premiums) of pet-insurance partner groups 12 months ahead, using **fully synthetic data**. Small groups don't have enough history to trust on their own, so each group's experience is blended with its segment's average using **actuarial credibility**. The project covers the whole workflow: a messy-data generator, a cleaning pipeline with a data-quality gate, the projection model, a back-test, an API, a React dashboard, monitoring, Docker and CI.

> All data is made up by `petlr.generate`. No real people, pets, insurers or claims are involved.

---

## 1. What it does

- **Generates** ~300 partner groups (10–2,000 pets each) over 24 months, with seasonality, an 8%/year vet-cost trend, random large claims and a late cost shock in one region. It also *deliberately plants* data problems: duplicate rows, pets from cancelled groups still listed, late-paid claims, dates shifted by one day, and bad start dates.
- **Cleans** the raw files into one row per group per month. A **pandera data-quality gate** reports every problem it caught, and fails loudly if anything unexpected gets through.
- **Projects** each group 12 months forward: `Z × own cost + (1 − Z) × segment benchmark`, with `Z = min(1, √(pet-months ÷ threshold))`, then applies trend, seasonality and a large-claim load.
- **Back-tests** the model by hiding the last 6 months, projecting them and measuring the error. It compares three versions: credibility blend, group's own data only, and benchmark only.
- **Serves** it all through **FastAPI**, with the assumptions as query parameters, and a **React dashboard** with sliders, headline tiles, a history + projection chart, credibility shown beside every number, at-risk flags and CSV export.
- **Monitors** data freshness, drift and projection-vs-actual; **Prefect** schedules it daily, **Docker Compose** runs everything with one command, and **GitHub Actions** tests every push.

## 2. Tools and libraries (and why)

| Tool | Why it was chosen |
|---|---|
| **pandas / NumPy** | Standard Python tools for tables and vectorized maths; everything here is tabular. |
| **pyarrow (Parquet)** | Clean data is saved as Parquet: typed, compressed and fast to reload (CSV loses dates and dtypes). |
| **pandera** | Declarative data-quality checks on DataFrames. `lazy=True` collects *every* failure instead of stopping at the first one, which is what "report every problem" needs. |
| **Prefect 3** | Orchestration: turns the pipeline into retryable, logged tasks with a UI and a cron schedule, with very little code. |
| **FastAPI + Uvicorn** | Typed query parameters with automatic validation (bad slider values get a 422) and free interactive docs at `/api/docs`. |
| **React + Vite** | React for the interactive UI; Vite for a fast dev server and an `/api` proxy. |
| **Recharts** | Simple React charting with custom tooltips, enough for one line chart without hand-writing D3. |
| **pytest / Vitest** | Unit tests for Python (with hand-calculated expected values) and for the JS helpers. |
| **Ruff** | Fast linter, run in CI. |
| **Docker Compose + nginx** | One command starts the Prefect server, the pipeline, the scheduler, the API and the website; nginx serves the site and proxies `/api`. |
| **GitHub Actions** | CI: lint, tests, pipeline smoke test, frontend build, secret scan (gitleaks) and a Compose smoke test. |
| **detect-secrets / gitleaks** | Secret scanning before anything goes public. |

## 3. File structure

```
pet-insurance-forecaster/
├── README.md                 This file
├── LEARNING.md               Each phase explained simply + 20 interview Q&As
├── requirements.txt          Python dependencies
├── pyproject.toml            Package config (src layout), pytest and ruff settings
├── .env.example              Placeholder settings (no secrets are needed)
├── .gitignore                Keeps .env, data/, caches, node_modules out of git
├── Dockerfile                Python image for pipeline, scheduler and API
├── .dockerignore             Keeps data/, frontend/ and caches out of the Python image
├── docker-compose.yml        Prefect server + pipeline + scheduler + API + website
├── .github/workflows/ci.yml  GitHub Actions: lint, tests, build, secret scan, Compose smoke test
├── src/petlr/
│   ├── __init__.py           Package marker
│   ├── config.py             Paths, constants, and the band/label helpers
│   ├── generate.py           Phase 1: synthetic data generator with planted problems
│   ├── quality.py            Phase 2: pandera schemas, issue report, DataQualityError
│   ├── pipeline.py           Phase 2: raw → clean monthly dataset, completion factors
│   ├── flows.py              Phase 2/7: Prefect flow (run once, or serve on a daily cron)
│   ├── projection.py         Phase 3: credibility-weighted projection + aggregation
│   ├── backtest.py           Phase 4: hide 6 months, project, score three models
│   ├── api.py                Phase 5: FastAPI app (group, segment, export, monitoring)
│   └── monitoring.py         Phase 7: freshness, PSI drift, segment drift, A/E checks
├── tests/
│   ├── conftest.py           Shared fixtures (one small generated dataset)
│   ├── test_generate.py      Generator shape, determinism and planted problems
│   ├── test_pipeline.py      Gate catches and fixes every planted problem; fails loudly
│   ├── test_projection.py    Credibility, trend, blend and projection, hand-calculated
│   ├── test_backtest.py      Zero error in a perfect world; no future leakage
│   ├── test_api.py           Endpoints, filters, assumptions, CSV export
│   └── test_monitoring.py    PSI, freshness levels, segment drift, A/E grading
└── frontend/
    ├── package.json          JS dependencies and scripts
    ├── vite.config.js        Dev server + /api proxy to FastAPI
    ├── index.html            Page shell
    ├── Dockerfile            Build the site, serve with nginx
    ├── .dockerignore         Keeps node_modules and dist out of the image
    ├── nginx.conf            Serves the site and proxies /api to the API container
    └── src/
        ├── main.jsx          React entry point
        ├── App.jsx           Page layout, state, debounced API calls
        ├── api.js            Builds query strings, fetches JSON, CSV export URL
        ├── format.js         Number/percent/money/month formatting
        ├── format.test.js    Vitest tests for helpers
        ├── styles.css        Design tokens (light + dark) and layout
        └── components/
            ├── Sliders.jsx       Assumption sliders
            ├── Filters.jsx       Segment filter chips
            ├── Tiles.jsx         Headline tiles, each with credibility
            ├── LossRatioChart.jsx History + projection chart with table view
            ├── Breakdown.jsx     Projected LR by region/mix/deductible/size/tenure
            ├── GroupsTable.jsx   Sortable groups table with Z meter and at-risk flag
            ├── GroupDetail.jsx   Step-by-step "how this number was built"
            ├── Backtest.jsx      Back-test results
            ├── Monitoring.jsx    Monitoring checks
            └── Bits.jsx          Credibility meter and at-risk flag
```

`data/` is created when you run the pipeline and is not committed: `raw/` (CSVs + `_planted.json`), `clean/` (Parquet), `out/` (quality report, back-test, monitoring, projection snapshots).

## 4. Setup

You need [Anaconda/Miniconda](https://www.anaconda.com/) and Node.js 18+ (22 recommended).

```bash
cd ~/projects/pet-insurance-forecaster

# Python environment (named after the folder)
conda create -n pet-insurance-forecaster python=3.12 -y
conda activate pet-insurance-forecaster
pip install -r requirements.txt
pip install -e .

# Website dependencies
cd frontend && npm install && cd ..

# Optional: copy the example settings (nothing secret in here)
cp .env.example .env
```

## 5. How to run it

**Run the whole pipeline once** (generate → clean + gate → projection snapshot → back-test → monitoring):

```bash
python -m petlr.flows
```

**Start the API and the website** (two terminals):

```bash
uvicorn petlr.api:app --reload --port 8000
```

```bash
cd frontend && npm run dev
```

Open **http://localhost:5173** for the dashboard and **http://localhost:8000/api/docs** for the API docs.

**Each step on its own:**

```bash
python -m petlr.generate      # writes data/raw/ (and prints what was planted)
python -m petlr.pipeline      # cleans, runs the gate, prints every issue caught
python -m petlr.backtest      # prints error for credibility vs own-only vs benchmark-only
python -m petlr.monitoring    # prints freshness / drift / projection-vs-actual checks
```

**Daily schedule with Prefect.** Schedules need a real Prefect server, not the temporary one:

```bash
prefect server start                                   # terminal 1, UI at http://localhost:4200
PREFECT_API_URL=http://127.0.0.1:4200/api python -m petlr.flows --serve   # terminal 2
```

**Everything with one command (Docker):**

```bash
docker compose up --build
```

Then open http://localhost:8080 (website), http://localhost:8000/api/docs (API) and http://localhost:4200 (Prefect UI).

**Tests:**

```bash
pytest                       # 45 Python tests
ruff check src tests         # lint
cd frontend && npm test      # JS helper tests
```

### Example API calls

```
GET /api/segments?region=West&deductible=500&trend=0.08&threshold=3000
GET /api/segments/breakdown?by=species_mix
GET /api/groups?at_risk_only=true&sort=-proj_lr
GET /api/groups/G0042?premium_change=0.05
GET /api/export.csv?size=small&size=medium
GET /api/backtest      GET /api/monitoring      GET /api/quality
```

## 6. How it was built (in order)

1. **Synthetic data generator.** Simulated groups, pets joining and leaving month by month, monthly bills and claim-level records with frequency × severity, seasonality, trend, large claims, payment lags and a West-region cost shock in the last 4 months. Then planted the five data problems and saved a ground-truth count of each.
2. **Pipeline + quality gate.** Wrote pandera schemas for what *perfect* raw data looks like (every failure becomes a reported issue), fixed each known problem, booked claims to the month of the vet visit, estimated **completion factors** to gross up recent months for claims not yet paid, and ran a strict schema (including a billed-pets = enrolled-pets reconciliation) on the output. Wrapped it in a Prefect flow.
3. **Projection.** Estimated trend year-over-year and a seasonality index, normalized each group's capped experience, blended it with its segment benchmark by credibility, added a large-claim load and rolled 12 months forward. Unit tests check hand-calculated numbers.
4. **Back-test.** Rebuilt history *as known at the cutoff* (no late payments from the future), projected the hidden 6 months and scored three models. This led to two fixes: the default threshold was tuned to 2,000 pet-months, and trend estimation switched to year-over-year.
5. **FastAPI.** Exposed group view, segment view (filters for species mix, region, deductible, tenure, size), a breakdown, CSV export, back-test and quality report, with assumptions as validated query parameters and cached projections.
6. **React website.** Sliders, headline tiles, history + projection chart (with a table view), credibility meter beside every number, at-risk flags (icon + label, not colour alone), a step-by-step group breakdown and CSV export. Light and dark themes; works at phone width.
7. **Monitoring, Docker, CI.** Added freshness, PSI drift, segment drift and projection-vs-actual checks, a Compose stack and a GitHub Actions workflow.
8. **Docs.** This README and LEARNING.md.

### Results on the default synthetic data (as of Sep 2026)

| | Value |
|---|---|
| Problems caught by the gate | 1,093 duplicate claims, 28 duplicate premium rows, 187 + 1,536 one-day date shifts, 5,450 cancelled-group pets, 7,680 late-paid claims, 7 bad start dates |
| Estimated trend | 7.9%/year (true value: 8%) |
| Projected 12-month loss ratio | ~73.5% |
| Back-test group error (WAPE) | credibility **21.4%** vs own-only 21.8% vs benchmark-only 26.6% |
| Back-test, small groups (Z < 0.3) | credibility **41%** vs own-only **69%** vs benchmark-only 43% |
| Monitoring | flags the planted West cost shock (+24% vs +12% elsewhere) |

The back-test also shows a **−7.6% portfolio bias**: the model under-projected the hidden months. Two causes: only 18 months of history were available at the cutoff, so the trend estimate came out at 3.9% (true value 8%), and the West shock started after the cutoff. LEARNING.md explains both.

## 7. Key concepts (interview-ready)

- **Loss ratio**: claims ÷ premium. Below ~70% the business is healthy for pet insurance; above 100% you pay out more than you collect.
- **PPPM (per pet per month)**: claims ÷ pet-months. Normalizes for group size so groups can be compared.
- **Credibility**: how much to trust a group's own experience. `Z = min(1, √(n / threshold))`, then `projection = Z × own + (1 − Z) × benchmark`. Small groups lean on the benchmark, big groups on themselves.
- **Trend and seasonality**: costs rise every year (trend) and peak in summer (seasonality). Remove both before comparing months, then put them back when projecting.
- **Large-claim capping**: cap each claim at $5k in the group's own experience and add large claims back as a portfolio-wide load, so one surgery doesn't distort a small group.
- **Completion factors (IBNR)**: recent months look cheap because claims arrive late. Gross them up using how complete older months were at the same age.
- **Back-testing**: hide recent data, project it, measure the error, and compare against simpler alternatives, using only data known at the cutoff.
- **Data-quality gate**: explicit, tested rules that stop bad data from reaching the model, and report everything they found.
- **Drift and monitoring**: PSI measures distribution shift; segment drift spots one region running hot; actual ÷ expected tells you when the model is wrong.

See **[LEARNING.md](LEARNING.md)** for each phase explained step by step, plus 20 interview questions with answers.

## Hosting it for free (Render)

The free tiers sleep when idle, so the first visit can take ~30–60 seconds.

1. Push this repo to GitHub.
2. **API**: on [render.com](https://render.com), create a **Web Service** → connect the repo → Runtime **Docker** → Instance type **Free**. Set the **Docker Command** to
   `sh -c "python -m petlr.generate && python -m petlr.pipeline && python -m petlr.backtest && python -m petlr.monitoring && uvicorn petlr.api:app --host 0.0.0.0 --port $PORT"`
   so the container builds its own synthetic data on start. Check `https://<api-name>.onrender.com/api/health`.
3. **Website**: create a **Static Site** → same repo → Root directory `frontend`, Build command `npm ci && npm run build`, Publish directory `dist`. Under **Redirects/Rewrites** add a rewrite from `/api/*` to `https://<api-name>.onrender.com/api/*`, and a rewrite from `/*` to `/index.html`.
4. Open the static site URL.

(The daily Prefect schedule is not part of the free setup; every restart regenerates fresh synthetic data instead.)
