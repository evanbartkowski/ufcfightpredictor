# MMA Cipher

UFC fight forecasts, source records, and a personal pick tracker. The production site is https://mma-oracle.web.app.

## Model claims and the deployed version

**Serving probabilities have not been separately calibrated.** The reliability chart reports empirical holdout bins; it does not fit or apply a calibration transform. Neither estimator agreement nor a high model probability is a guarantee of an outcome.

The loaded model bundle supplies estimator names, blend weights, input names and counts, version, source-data cutoff, holdout metrics, and calibration status. The homepage, `/model/`, `/api/model`, and `/api/dashboard` use this metadata. They do not maintain a separate architectural or feature-count claim. The published code commit, job URL, state digest, and persisted table counts are available at `/status/` and `/api/health`.

`predictor/model.py` currently fits Extra Trees, Random Forest, XGBoost, LightGBM, HistGradientBoosting, and a standardized logistic component. Consult the deployed metadata for the actual fitted estimators and input count. Code changes invalidate cached model bundles using an implementation hash; version IDs also include the training data and blend weights.

## Evaluation and limitations

- Fighter histories are replayed by event date. A bout's own outcome, other outcomes on the same day, and later results do not update its input state.
- The final 20% of event dates form the chronological holdout. Holdout accuracy, Brier score, log loss, and reliability-bin counts are measured on models fitted before that period.
- Blend weights use candidate accuracy minus 0.25 times Brier score on the 65%-80% validation window, a softmax transform, and a cap on the logistic component before normalization. Three expanding-window log losses are diagnostics and select a single-estimator fallback; the blend does not use Nelder-Mead optimization.
- Production estimators are refitted on all available decided bouts after evaluation. Their held-out metrics describe the separate historical evaluation, not an independently observed future success rate.
- Historical attribute timestamps, imported rankings, and source completeness are not independently audited. Missing measurements use baseline values. The chronology of replay is not a blanket guarantee against every source of leakage.
- Draws and no contests are not binary win/loss training targets and do not move replay Elo. Reported UFC records describe dataset coverage; career profile records are separate snapshots.
- Method-of-victory estimates use a separate multiclass logistic model. If that model is unavailable, no invented probability split is displayed.

## Prediction evidence

Each eligible upcoming prediction reports its three strongest supporting input sensitivities, a major uncertainty, and the decided UFC bout count for each fighter. These sensitivities are computed from the full serving ensemble by setting one differential input to zero at a time. They are non-additive, depend on correlated inputs, and are not causal effects or SHAP values. The global coefficient chart is explicitly limited to the logistic component.

Original issued forecasts remain immutable. Latest forecasts may update before the event starts. Event-start locks prevent later model changes or explanations from rewriting a settled prediction. Older frozen snapshots retain their original estimates and explicitly disclose when full-ensemble explanations were not recorded.

The live prediction record grades original **main-card picks only**. Prelims, early prelims, and bouts without verified card placement are excluded. Main-card draws and no contests appear as unscored and do not affect accuracy. Card placement is archived from matched ESPN broadcast blocks, with historical lookups for older issued picks; this metadata survives fresh updater runs without editing the original forecasts. Historical model holdout metrics still describe the full chronological evaluation sample.

## Personal picks

Picks are stored only in the current browser using `mma_cipher_user_picks`. Selecting a fighter records a personal choice; selecting it again clears it. There is no shared voting backend, synthetic starting vote count, or claimed community consensus. Existing personal selections are preserved.

## Hosting and unattended updates

Firebase Hosting serves pre-rendered HTML and JSON files. It does not execute Flask, SQLite, or Python background threads.

`.github/workflows/refresh-site.yml` runs on pushes to `main`, manual dispatch, and a schedule every 15 minutes. GitHub can delay scheduled runs. Each run restores the last published public state, refreshes odds, pulls UFCStats when the last successful stats update is at least six hours old, prepares a complete publication, verifies it, and deploys it atomically. Failed source pulls preserve saved records and their old timestamps and display the failure. A failed state restore prevents deployment.

The next run restores `api/sync-state.json.gz`, which contains public verified bouts, profile snapshots, and original/latest prediction revisions. The runner's model cache is an optimization, not the durable data store. Original predictions survive missing caches and runner replacement. The state excludes credentials, local settings, and watched-fighter browsing activity. A state digest and table counts allow the deployed data to be checked independently.

Google Workload Identity Federation restricts the dedicated Hosting publisher to this repository ID and `main`; no long-lived personal Firebase token or service-account key is stored in GitHub. The updater retains 24 recent automated snapshots, protects manual releases and active content, and verifies the deployed APIs, HTML, model metadata, and saved state after publishing.

The browser checks a small revision response every minute, downloads a compact overview only when it changes, and loads fighter details and historical analytics on demand. It retries network failures. The initial HTML already contains the latest upcoming card, counts, source timestamps, and model description. It remains readable without JavaScript.

## Searchable records

The export and Flask app share the same templates and catalog:

- `/events/` and individual event pages contain announced cards and verified results.
- `/fighters/` and individual fighter pages contain career snapshots, UFC dataset records, opponent history, replay Elo charts and exact values, recent performance, upcoming fights, and striking/grappling comparisons where opponent profiles exist.
- `/fights/` and individual published-forecast pages retain issued predictions, evidence, uncertainty, and available results.
- `/model/` and `/status/` expose the measured methods and production data state.

Pages include canonical URLs, internal links, and structured data where applicable. `sitemap.xml` and `robots.txt` are generated with each publication. Historical bouts without an issued forecast remain accessible in event and fighter histories; no retrospective forecast is invented for them.

## Run locally

```powershell
.\start.ps1
```

Open http://localhost:8000. The Flask process starts its background schedulers. To serve saved data without network refresh:

```powershell
.\.venv\Scripts\python app.py --no-sync
```

A one-time public export, including indexable pages and production health metadata:

```powershell
.\.venv\Scripts\python scripts/refresh_site.py --restore-url https://mma-oracle.web.app/api/sync-state.json.gz
.\.venv\Scripts\python scripts/verify_publication.py
```

The hosted dashboard and Matchup Lab can display issued forecasts for announced pairs. Arbitrary hypothetical Matchup Lab requests require the running Flask API.

## Validation

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python tests/browser_updates.py
.\.venv\Scripts\python tests/browser_smoke.py
.\.venv\Scripts\python tests/browser_indicators.py
.\.venv\Scripts\python scripts/verify_publication.py --url https://mma-oracle.web.app
```

Browser checks require the local server. Generated records pages, runtime state, model binaries, local credentials, and browser captures are excluded from Git.

## Input integrity and market comparison

The serving model is now **market-free**. Its feature list excludes the two historical moneyline inputs. Bare CSV moneylines and unverified nested odds cannot affect either its training vectors or live estimates.

A separate paired research comparison uses the same timestamp-verified bouts, chronological holdout dates, and standardized logistic estimator for both market-free and odds-aware variants. A snapshot must match the exact dated pair, precede the event start, and be observed within 24 hours of that start. Until 100 such bouts across 10 event dates exist, metadata reports `insufficient_data`, with no invented comparison score. Eligible pre-event snapshots are preserved in public updater state for future completed bouts.

Current fighter profiles keep raw display values plus typed normalization. Heights and reaches become centimetres, weights become pounds, percentages become fractions, and birthdates yield age on the event date. Missing and non-finite measurements stay missing. Scheduled and hypothetical predictions merge only profiles observed before issue time and event start. Historical replay never reads current fighter profiles. The issued forecast records eligible input fields and observation timestamps.

Replay examples carry their original fight IDs, event names, and divisions through chronological filtering. Draws, no contests, and input ordering cannot shift later holdout labels. Multiple events on the same date retain separate event attribution. Empty reliability bins expose null measurements rather than zero observed accuracy.

## Serving performance and APIs

`Service.dashboard()` computes one shared snapshot per database revision, model version, status change, or minute of freshness aging. `/api/dashboard` serves cached JSON bytes; committed writes invalidate the cache, while rolled-back writes do not. A cache miss reads all required tables and metadata in one SQLite snapshot. Callers must treat the shared computed dictionary as read-only; publication explicitly copies it before adding export metadata.

SQLite retains WAL. Short request scopes reuse one connection and close it at teardown. Related metadata and prediction writes use transactions; network scraping does not hold an open write transaction. Indexed normalized fighter IDs resolve matchup participants, and an indexed fighter-bout relation supports bounded history queries. Fighter search enriches only its requested page using a bounded window query.

| Purpose | Python service | Static Hosting |
| --- | --- | --- |
| Compact initial data | `/api/overview` | `/api/overview` |
| Archive finder | `/api/search-index.json` | `/api/search-index.json` |
| Fighter search and pages | `/api/fighters?q=Silva&page=1&per_page=48` | Name index in overview, then `/api/fighters/page/1.json` |
| Fighter lookup/history | `/api/fighters/MaxHolloway` and `/api/fighters/MaxHolloway/history` | Published profile JSON and indexable fighter pages |
| Forecast pages | `/api/predictions?kind=upcoming&page=1&per_page=48` | `/api/predictions/upcoming/page/1.json` |
| Retrospective analytics | `/api/analytics?kind=events&page=1&per_page=100` | `/api/analytics/events/page/1.json` |
| Holdout bout attribution | `/api/analytics?kind=holdout&page=1` | `/api/analytics/holdout/page/1.json` |

Page sizes are bounded at 100. Static Hosting does not execute query-based Flask APIs: its browser searches the small name index and fetches deterministic page files. The compatibility dashboard remains available, but new browser loads do not download its full fighter histories. Legacy browser cache data migrates once; only a smaller current-format payload is retained.

Fighter database contains the event, profile, forecast, and model collections. Its archive finder loads the cross-collection search index on demand. Event search covers upcoming and archived cards, including lineup names; forecast search supports event/date/model queries and status filters. The Model Watch control filters upcoming matchups without adding alert banners to fight cards.

The mostly black visual theme uses self-hosted Barlow Condensed, Manrope, and IBM Plex Mono fonts (OFL licenses and asset provenance are in `static/fonts/`). Decorative SVG cage mesh and circuit paths accompany a custom canvas octagon projection. Canvas drawing is capped at 24 fps, stops when the page is hidden, and renders a still view when reduced motion is requested. The background never receives pointer events; no simulated visual is used as prediction data.

`/api/matchup` validates name types and lengths, applies per-IP and process-wide limits, and bounds concurrent inference. It uses the socket address rather than trusting arbitrary forwarded-IP headers. Profile refreshes are deduplicated; all source sessions share a bounded request budget. A multi-process or multi-host deployment should use a shared rate-limit service instead of treating these process-local limits as a distributed quota.

## Reproducible deployment and readiness

`requirements.in` and `requirements-dev.in` are resolver inputs. The checked-in `.txt` files pin direct and transitive versions with artifact hashes for Python 3.12 and newer. Regenerate them together with:

```powershell
uv pip compile requirements.in --python-version 3.12 --universal --generate-hashes --output-file requirements.txt
uv pip compile requirements-dev.in --constraint requirements.txt --python-version 3.12 --universal --generate-hashes --output-file requirements-dev.txt
```

The Docker base image is pinned by digest. CI performs a clean, uncached Docker build, installs hash-verified binary dependencies, runs `pip check`, and tests both prepared and unprepared server processes. Pull requests run the clean container checks without publishing. Production deployment requires those checks to pass.

Model training is explicit, outside application initialization:

```powershell
.\.venv\Scripts\python app.py --prepare-model
.\.venv\Scripts\python app.py --no-sync
```

The image prepares serving state during its build, then installs that state only into an empty runtime directory. Existing user databases are not replaced. The web factory never synchronously trains a model. With automatic updates enabled, a missing model can be rebuilt in a background worker; liveness remains available while readiness is unavailable.

- `/health` is process liveness and remains HTTP 200 without a model.
- `/ready` returns HTTP 503 if the model or required SQLite schema is unavailable, and HTTP 200 when serving dependencies are usable.
- `/api/health` on the public static site describes the last publication and updater state, not a continuously running Flask process.

UFCStats requests prefer HTTPS and validate every redirect and host. The upstream currently refuses TLS connections from this environment; a cached, same-host HTTP fallback is permitted only for connection failures. Certificate failures and HTTP errors are not silently downgraded. Cached data survives source failures and rate-limit responses.

## Load testing and monitoring

```powershell
.\.venv\Scripts\python scripts/benchmark_api.py
.\.venv\Scripts\python tests/browser_api.py
.\.venv\Scripts\python tests/browser_design.py
.\.venv\Scripts\python scripts/monitor_production.py
```

The benchmark reports concurrent WSGI p50/p95 latency, payload bytes, resident memory, incremental Python allocations, cache rebuilds, SQL statements, and connection counts. It makes no upstream HTTP requests. These development measurements are not an Internet capacity guarantee; see `reports/api_performance_2026-11-01.json` for the measured workload and baseline.

The freshness monitor fails if publication or odds checks are older than 45 minutes, or a successful fight-data pull is older than seven hours. The browser also identifies delayed publication heartbeats instead of describing an old snapshot as a fresh update. An hourly GitHub monitor workflow runs this check; the same CLI can run on an independent monitoring service. GitHub schedules can be delayed or dropped, so the monitor workflow itself is not independent of GitHub scheduling. Production metadata records the trigger event and job URL; a successful push/manual run alone is not proof that a timed trigger fired.
