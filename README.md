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

## Personal picks

Picks are stored only in the current browser using `mma_cipher_user_picks`. Selecting a fighter records a personal choice; selecting it again clears it. There is no shared voting backend, synthetic starting vote count, or claimed community consensus. Existing personal selections are preserved.

## Hosting and unattended updates

Firebase Hosting serves pre-rendered HTML and JSON files. It does not execute Flask, SQLite, or Python background threads.

`.github/workflows/refresh-site.yml` runs on pushes to `main`, manual dispatch, and a schedule every 15 minutes. GitHub can delay scheduled runs. Each run restores the last published public state, refreshes odds, pulls UFCStats when the last successful stats update is at least six hours old, prepares a complete publication, verifies it, and deploys it atomically. Failed source pulls preserve saved records and their old timestamps and display the failure. A failed state restore prevents deployment.

The next run restores `api/sync-state.json.gz`, which contains public verified bouts, profile snapshots, and original/latest prediction revisions. The runner's model cache is an optimization, not the durable data store. Original predictions survive missing caches and runner replacement. The state excludes credentials, local settings, and watched-fighter browsing activity. A state digest and table counts allow the deployed data to be checked independently.

Google Workload Identity Federation restricts the dedicated Hosting publisher to this repository ID and `main`; no long-lived personal Firebase token or service-account key is stored in GitHub. The updater retains 24 recent automated snapshots, protects manual releases and active content, and verifies the deployed APIs, HTML, model metadata, and saved state after publishing.

The browser checks a small revision response every minute, downloads the full dashboard only when it changes, and retries network failures. The initial HTML already contains the latest upcoming card, counts, source timestamps, and model description. It remains readable without JavaScript.

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

The hosted dashboard provides published forecasts. Arbitrary hypothetical Matchup Lab requests require the running Flask API.

## Validation

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python tests/browser_updates.py
.\.venv\Scripts\python tests/browser_smoke.py
.\.venv\Scripts\python tests/browser_indicators.py
.\.venv\Scripts\python scripts/verify_publication.py --url https://mma-oracle.web.app
```

Browser checks require the local server. Generated records pages, runtime state, model binaries, local credentials, and browser captures are excluded from Git.
