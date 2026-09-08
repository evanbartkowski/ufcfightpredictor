# MMA Oracle Predictions

A UFC prediction website rebuilt from the original notebook: upcoming cards, winner probabilities, current fighter profiles, a custom matchup lab, and an honest prediction record.

## Run on Windows

Install Python 3.12 or newer, then run from this repository:

```powershell
.\start.ps1
```

Open **http://localhost:8000**. The first run installs dependencies, imports the bundled history, and trains the model. The server refreshes UFCStats in the background immediately, then every hour. Sportsbook odds refresh independently every 15 minutes. The browser checks for updates every 60 seconds. The first refresh backfills results since December 2024 and can take several minutes. Leave the server running for automatic updates.

Manual setup (on macOS/Linux, use `.venv/bin/python`):

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m playwright install chromium
.\.venv\Scripts\python app.py
```

Serve cached data without network access:

```powershell
.\.venv\Scripts\python app.py --no-sync
```

Refresh once and exit:

```powershell
.\.venv\Scripts\python app.py --sync-once
```

Use one application process per data directory. Do not run `--sync-once` alongside the automatic server. If using an external scheduler, run the website with `--no-sync` and restart it after an external retrain to load the new model.

## Hosting

The responsive HTML/CSS/JavaScript UI is served by Flask and Waitress. No frontend build, account, paid API key, or external font service is required.

```sh
docker compose up --build -d
```

This exposes port 8000 and persists data in the `predictor-data` volume. On a hosting provider, deploy the Dockerfile, attach a persistent disk at `/app/data`, and use the provider's HTTPS endpoint. An always-running instance is required for automatic updates; static/sleeping hosting cannot run this pipeline.

| Variable | Default | Purpose |
| --- | --- | --- |
| HOST | 127.0.0.1 (0.0.0.0 in Docker) | Listen address |
| PORT | 8000 | HTTP port |
| DATA_DIR | ./data | Persistent database and model |
| SYNC_INTERVAL_SECONDS | 3600 | Cards, results, and fighter stats; minimum 180 seconds |
| ODDS_INTERVAL_SECONDS | 900 | Sportsbook odds; minimum 60 seconds |

API: `GET /health`, `GET /api/dashboard`, and `POST /api/matchup` with JSON `{"red":"Max Holloway","blue":"Conor McGregor"}`. Expensive refresh/training operations are not exposed as public HTTP endpoints.

## Data and learning loop

1. Import dated outcomes from `ufc-master.csv`; preserve the original notebook and CSVs.
2. Fetch the official UFCStats completed and upcoming event indexes.
3. Backfill missing results and revisit the last 30 days of known events for delayed outcomes and corrections.
4. Refresh current profiles for fighters on upcoming cards and cards from the last 14 days. Fighters selected in the matchup lab also refresh in the background and stay on a 30-minute watchlist. Other fighters retain labeled archived profiles.
5. Retrain when confirmed results change. Draws and no contests are stored but excluded from binary training/scoring.
6. Refresh the displayed forecast with the newest model and fighter snapshots on every update until event start. Save the original for scoring, the latest version for display, and an audit revision whenever model inputs or profile values change. Repeated identical inputs do not add audit rows.
7. Compare each confirmed result with the saved forecast, then incorporate it into future training.

Forecasts update until the **earliest scheduled start of the event** from ESPN, including on event day. If no dependable start time is available, freeze at UTC midnight on event day. In-progress or completed events cannot generate new pre-event forecasts. Saved forecasts remain visible while awaiting results. A changed opponent/date is a new pairing. Removed pairings disappear from the card and remain unscored. Corrections older than the 30-day revisit window require a deliberate historical backfill.

Source requests are throttled, use timeouts/retries, and reuse cookies. Chromium handles the site's ordinary JavaScript browser check when necessary. No CAPTCHA solver is included. Invalid/challenge pages never count as a successful empty update. Refresh errors and cached-data freshness are visible in the UI.

## Model and evaluation

The original notebook had target leakage: some models used strikes, takedowns, knockdowns, and control time **from the fight being predicted**. Other versions merged current career totals into older fights or fitted preprocessing before splitting. Those scores do not measure pre-fight performance.

The replacement reconstructs each fighter's state strictly from earlier calendar dates:

- Opponent-adjusted Elo.
- Recorded wins, losses, experience, and signed streak.
- Smoothed recent-five-fight form.
- Recorded KO/submission win rates.
- Days since the previous recorded fight.

Inputs are fighter-to-fighter differences. Mirrored training examples and symmetric probability averaging ensure that swapping fighter order preserves the forecast.

Model candidates are selected by mean log loss over three expanding chronological validation windows (45%-55%, 55%-65%, and 65%-80% of event dates). Baseline and recent-performance logistic regression at two regularization levels compete with baseline gradient boosting. The selected approach is refitted on the first 80% and evaluated on the final 20%; the serving model is then fitted on all confirmed results. Preprocessing is fitted only on each training partition. Holdout accuracy, Brier score, log loss, and Elo/majority baselines are shown separately from live accuracy. Evaluation is recomputed when data changes, so successive scores are not a fixed benchmark.

**Current striking/grappling profiles are displayed for comparison, not merged into historical training rows.** Their past values cannot safely be recovered from today's career totals. This model uses consistent, auditable pre-fight results features. The bundled record begins in 2010, so early careers and non-UFC experience are incomplete. Fighters with fewer than three recorded bouts are flagged; two unknown fighters receive 50/50. Probabilities have not been separately calibrated. Fixing evaluation and automation does not establish superiority over the notebook's incomparable scores.

## Tests

```powershell
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest -q
```

Tests cover pre-fight/same-day leakage, swapped fighter order, draws/no contests, repeated syncing, immutable forecasts, result corrections, parsing, and API validation. Tests do not require network access. Verify provider availability separately with `--sync-once`.

Runtime artifacts are stored in the Git-ignored `data/` directory. Back it up to preserve issued forecasts. Only load joblib models generated by this application: joblib is executable Python serialization.

Sources: [UFCStats event data](http://ufcstats.com/statistics/events/completed?page=all), [scikit-learn chronological evaluation guidance](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html).

## Sportsbook odds and freshness

DraftKings moneylines are fetched from ESPN's public UFC feed without an API key. Only prices actually published by the provider are shown; missing markets display an unavailable state. Fighter athlete IDs determine which moneyline belongs to which side. A small, explicit alias map handles verified naming differences across providers; ambiguous names or rematches never receive guessed odds.

The card shows the bookmaker, each fighter's American moneyline, and when this application fetched it. ESPN does not supply a reliable line-change timestamp for this feed, so a fetch timestamp is not represented as the time the bookmaker last changed its price. Odds are comparison data, not model inputs or wagering recommendations.

Odds refresh on their own worker every 15 minutes, separately from the hourly card/stats/results refresh. Jobs do not overlap within a worker. A refresh that takes longer than its interval skips overlapping starts. The actual cadence therefore depends on source response time. Failed odds requests keep the old timestamp and flag cached prices as stale. Failed fighter profile requests retain and label the cached profile without blocking new opponents/cards. Both event indexes must succeed before replacing the UFCStats schedule.

The app cannot update faster than the sources publish data, and it must remain running. Source availability and unannounced sportsbook markets can prevent a fresh value. The browser polls every 60 seconds without caching API responses; selected event filters and expanded fighter comparisons survive refreshes. An open custom matchup refreshes its model prediction and profile display too.

Forecast storage: `predictions` retains the original scored forecast; `latest_predictions` serves the newest eligible prediction; `prediction_revisions` records substantive changes with pre-event fighter snapshots. Original predictions already saved by earlier app versions remain intact. Results continue to score the original forecast for a consistent historical record.

Odds sources: [ESPN UFC schedule](https://www.espn.com/mma/schedule/_/league/ufc), [ESPN public UFC scoreboard](https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard). This public feed has no availability guarantee.

Each scheduled fight displays a countdown to the earliest event start supplied by ESPN, with the time in your browser timezone. It ticks locally every second without extra network requests. Individual bout times can vary; unknown starts show a to-be-announced state and elapsed timers never go negative.


## Automatic updates and card indicators

Normal app-factory and CLI startup enable background cards/results/stats refresh (hourly), odds refresh (15 minutes), and automatic retraining on changed confirmed outcomes. The browser refreshes each minute and on reconnect or returning to the tab, with request timeouts and overlap prevention. `--no-sync` and `--sync-once` retain their explicit offline/one-shot behavior. Run one application process per data directory; worker locks are process-local. Docker Compose already uses `restart: unless-stopped` for unattended hosting. The host and server must remain running with network access.

Women's divisions receive a subtle violet card background, based only on the published division label. The amber Model edge badge requires a predicted winner probability of at least 70% and a margin of at least 5 percentage points above the selected moneyline's break-even probability. It chooses the best qualifying available bookmaker price and excludes limited-history forecasts, missing predictions, stale or failed odds, and fights without a confirmed future pre-event start. For positive American odds A, break-even probability is 100/(A+100); for negative odds it is abs(A)/(abs(A)+100). This is a model-price comparison, not proof of profitability; probabilities are not separately calibrated. No badge is shown if nothing qualifies.

Run `python tests/browser_indicators.py` for offline browser checks of thresholds, side mapping, stale/live exclusions, division styling, and mobile layout.


## Recent-performance model update

Schema 3 adds five prior-outcome features: recent performance relative to Elo expectations, smoothed career win rate, finish-loss frequency, last-three-fight form, and logarithmic inactivity. All inputs precede the bout date; same-day results remain excluded. Current career snapshots and sportsbook odds remain outside training.

Candidate selection now averages log loss across three expanding date windows (45%-55%, 55%-65%, 65%-80%). Baseline and enriched logistic regression at two regularization levels and baseline gradient boosting compete. The final 20% remains outside model selection. Automatic result-driven retraining repeats this process; a new schema invalidates the old serving cache without changing saved forecasts.

On the current 1,555-fight holdout, the selected recent-performance logistic regression improved log loss from 0.66257 to 0.66202 and Brier score from 0.23507 to 0.23484. Accuracy decreased from 61.09% to 60.77%. These are small descriptive differences, not evidence of statistical significance, calibration, future profitability, or a guaranteed improvement. See `reports/model_evaluation.json` for the complete comparison. This holdout has been inspected; further research needs new forward evaluation rather than repeated tuning to it.
