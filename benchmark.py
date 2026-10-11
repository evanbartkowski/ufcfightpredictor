import csv, unicodedata
from collections import defaultdict
from itertools import groupby
from datetime import date
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier, ExtraTreesClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from scipy.optimize import minimize
import lightgbm as lgb
import xgboost as xgb

def identity(name):
    return ''.join(c for c in unicodedata.normalize('NFKD', name).casefold() if c.isalnum())

with open('ufc-master.csv', 'r', encoding='utf-8', errors='ignore') as f:
    master_rows = list(csv.DictReader(f))

def fresh():
    return dict(elo=1500., wins=0, losses=0, streak=0, recent=[], recent3=[], ko=0, sub=0, dec=0,
                last=None, residuals=[], finish_losses=0, total_rounds=0)

canonical = []
for row in master_rows:
    red, blue = row['RedFighter'], row['BlueFighter']
    winner = row['Winner'].lower()
    method = row['Finish']
    day = row['Date']
    if identity(red) > identity(blue):
        red, blue = blue, red
        winner = 'blue' if winner == 'red' else 'red'
        r_age, b_age = float(row['BlueAge']), float(row['RedAge'])
        r_h, b_h = float(row['BlueHeightCms']), float(row['RedHeightCms'])
        r_r, b_r = float(row['BlueReachCms']), float(row['RedReachCms'])
        r_st, b_st = row['BlueStance'], row['RedStance']
    else:
        r_age, b_age = float(row['RedAge']), float(row['BlueAge'])
        r_h, b_h = float(row['RedHeightCms']), float(row['BlueHeightCms'])
        r_r, b_r = float(row['RedReachCms']), float(row['BlueReachCms'])
        r_st, b_st = row['RedStance'], row['BlueStance']
    canonical.append(dict(date=day, red=red, blue=blue, winner=winner, method=method,
                          r_age=r_age, b_age=b_age, r_h=r_h, b_h=b_h, r_r=r_r, b_r=b_r,
                          r_st=r_st, b_st=b_st))

states = defaultdict(fresh)
examples = []
ordered = sorted(canonical, key=lambda f: (f['date'], f['red'], f['blue']))

for day, group in groupby(ordered, key=lambda f: f['date']):
    group = list(group)
    for f in group:
        a = states[identity(f['red'])]
        b = states[identity(f['blue'])]
        n_a, n_b = a['wins'] + a['losses'], b['wins'] + b['losses']
        rest_a = min((date.fromisoformat(day) - date.fromisoformat(a['last'])).days, 1095) if a['last'] else 365
        rest_b = min((date.fromisoformat(day) - date.fromisoformat(b['last'])).days, 1095) if b['last'] else 365
        
        elo_diff = a['elo'] - b['elo']
        wins_diff = a['wins'] - b['wins']
        losses_diff = a['losses'] - b['losses']
        streak_diff = a['streak'] - b['streak']
        recent_a = (sum(a['recent']) + 1) / (len(a['recent']) + 2)
        recent_b = (sum(b['recent']) + 1) / (len(b['recent']) + 2)
        ko_diff = (a['ko'] / max(n_a, 1)) - (b['ko'] / max(n_b, 1))
        sub_diff = (a['sub'] / max(n_a, 1)) - (b['sub'] / max(n_b, 1))
        exp_diff = n_a - n_b
        rest_diff = rest_a - rest_b
        res_a = np.mean(a['residuals']) if a['residuals'] else 0.
        res_b = np.mean(b['residuals']) if b['residuals'] else 0.
        smooth_a = (a['wins'] + 2) / (n_a + 4)
        smooth_b = (b['wins'] + 2) / (n_b + 4)
        floss_a = a['finish_losses'] / (n_a + 4)
        floss_b = b['finish_losses'] / (n_b + 4)
        rec3_a = (sum(a['recent3']) + 1) / (len(a['recent3']) + 2)
        rec3_b = (sum(b['recent3']) + 1) / (len(b['recent3']) + 2)
        loginact_diff = np.log1p(rest_a) - np.log1p(rest_b)
        
        age_diff = f['r_age'] - f['b_age']
        height_diff = f['r_h'] - f['b_h']
        reach_diff = f['r_r'] - f['b_r']
        st_val = lambda s: 1.0 if 'southpaw' in s.lower() else (0.5 if 'switch' in s.lower() else 0.0)
        stance_diff = st_val(f['r_st']) - st_val(f['b_st'])
        rounds_diff = a['total_rounds'] - b['total_rounds']
        
        feat = np.array([
            elo_diff, wins_diff, losses_diff, streak_diff, recent_a - recent_b,
            ko_diff, sub_diff, exp_diff, rest_diff,
            res_a - res_b, smooth_a - smooth_b, floss_a - floss_b,
            rec3_a - rec3_b, loginact_diff,
            age_diff, height_diff, reach_diff, stance_diff, rounds_diff
        ], dtype=float)
        
        y = 1 if f['winner'] == 'red' else 0
        examples.append((day, feat, y))
        
    for f in group:
        a = states[identity(f['red'])]
        b = states[identity(f['blue'])]
        y = 1 if f['winner'] == 'red' else 0
        expected = 1 / (1 + 10 ** ((b['elo'] - a['elo']) / 400))
        delta = 24 * (y - expected)
        a['elo'] += delta
        b['elo'] -= delta
        for state, won, expectation in [(a,y,expected),(b,1-y,1-expected)]:
            state['residuals'] = (state['residuals'] + [won - expectation])[-5:]
            state['finish_losses'] += int(not won and any(m in f.get('method','').upper() for m in ('KO','SUB')))
            state['wins'] += won
            state['losses'] += 1 - won
            state['streak'] = max(0, state['streak']) + 1 if won else min(0, state['streak']) - 1
            state['recent'] = (state['recent'] + [won])[-5:]
            state['recent3'] = (state['recent3'] + [won])[-3:]
            state['ko'] += int(won and 'KO' in f.get('method','').upper())
            state['sub'] += int(won and 'SUB' in f.get('method','').upper())
            state['last'] = day
            state['total_rounds'] += 3

days = sorted({e[0] for e in examples})
test_start = days[int(len(days)*.8)]
x = np.array([e[1] for e in examples])
y = np.array([e[2] for e in examples])
dates = np.array([e[0] for e in examples])
te = dates >= test_start
x_tr, y_tr = x[~te], y[~te]
x_te, y_te = x[te], y[te]

# Mirroring for symmetric invariance
x_tr_sym = np.concatenate([x_tr, -x_tr])
y_tr_sym = np.concatenate([y_tr, 1 - y_tr])

def sym_predict(model, x_eval):
    return (model.predict_proba(x_eval)[:, 1] + 1 - model.predict_proba(-x_eval)[:, 1]) / 2

# Models
models = {
    'Logistic Regression': make_pipeline(StandardScaler(), LogisticRegression(C=0.01, max_iter=1000)),
    'HistGradientBoosting': HistGradientBoostingClassifier(max_iter=120, max_leaf_nodes=12, learning_rate=0.03, l2_regularization=10, random_state=42),
    'LightGBM': lgb.LGBMClassifier(n_estimators=100, num_leaves=15, learning_rate=0.03, reg_lambda=5.0, verbose=-1, random_state=42),
    'XGBoost': xgb.XGBClassifier(n_estimators=100, max_depth=3, learning_rate=0.03, reg_lambda=5.0, eval_metric='logloss', random_state=42),
    'Random Forest': RandomForestClassifier(n_estimators=150, max_depth=6, min_samples_leaf=10, random_state=42),
    'Extra Trees': ExtraTreesClassifier(n_estimators=150, max_depth=6, min_samples_leaf=10, random_state=42),
    'MLP Neural Net': make_pipeline(StandardScaler(), MLPClassifier(hidden_layer_sizes=(32, 16), alpha=0.05, max_iter=300, random_state=42))
}

preds = {}
for name, m in models.items():
    m.fit(x_tr_sym, y_tr_sym)
    p = sym_predict(m, x_te)
    preds[name] = p
    acc = accuracy_score(y_te, p >= 0.5)
    ll = log_loss(y_te, p, labels=[0, 1])
    brier = brier_score_loss(y_te, p)
    print(f'{name:25s} Acc: {acc*100:.2f}% | LogLoss: {ll:.4f} | Brier: {brier:.4f}')

# Blended Ensemble: Equal weight
p_ensemble = np.mean(list(preds.values()), axis=0)
acc_ens = accuracy_score(y_te, p_ensemble >= 0.5)
ll_ens = log_loss(y_te, p_ensemble, labels=[0, 1])
brier_ens = brier_score_loss(y_te, p_ensemble)
print(f'Ensemble (Mean Blended)   Acc: {acc_ens*100:.2f}% | LogLoss: {ll_ens:.4f} | Brier: {brier_ens:.4f}')

# Elo baseline
elo_te = 1 / (1 + 10 ** (-x_te[:, 0] / 400))
acc_elo = accuracy_score(y_te, elo_te >= 0.5)
print(f'Elo Baseline              Acc: {acc_elo*100:.2f}%')

