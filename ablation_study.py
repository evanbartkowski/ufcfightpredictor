"""Ablation study starting directly from predictor/model.py's exact feature pipeline.
Measure baseline, test candidate feature additions one-by-one, tune hyperparameters,
and test stacking and market integration.
"""
import copy
from predictor.store import Store
from predictor.model import replay_with_methods, FEATURES, fit_symmetric, probability, make_ensemble_factories
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier, ExtraTreesClassifier
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from scipy.optimize import minimize
import lightgbm as lgb
import xgboost as xgb

store = Store('data')
fights = store.rows('fights')
print(f"Loaded {len(fights)} fights from store.")

canonical = []
for f in fights:
    # Keep exact canonical sorting as model.py
    canonical.append(f)

examples, states = replay_with_methods(canonical)
days = sorted({e[0] for e in examples})
val_start, test_start = days[int(len(days) * .65)], days[int(len(days) * .8)]

x_base = np.array([e[1] for e in examples])
y_base = np.array([e[2] for e in examples])
dates = np.array([e[0] for e in examples])

tr_mask = dates < val_start
va_mask = (dates >= val_start) & (dates < test_start)
te_mask = dates >= test_start

print(f"Holdout fights: {te_mask.sum()}, Train fights: {(~te_mask).sum()}")

x_tr, y_tr = x_base[~te_mask], y_base[~te_mask]
x_te, y_te = x_base[te_mask], y_base[te_mask]

# Baseline evaluation with exact model.py models
base_factories = {
    'Logistic Regression': lambda: make_pipeline(StandardScaler(), LogisticRegression(C=0.01, max_iter=1000, random_state=42)),
    'HistGradientBoosting': lambda: HistGradientBoostingClassifier(max_iter=100, max_leaf_nodes=8, learning_rate=0.03, l2_regularization=20.0, random_state=42),
    'LightGBM': lambda: lgb.LGBMClassifier(n_estimators=90, num_leaves=8, learning_rate=0.03, reg_lambda=20.0, verbose=-1, random_state=42),
    'Random Forest': lambda: RandomForestClassifier(n_estimators=180, max_depth=5, min_samples_leaf=20, random_state=42)
}

preds_te = {}
for name, fact in base_factories.items():
    m = fit_symmetric(fact(), x_tr, y_tr)
    p = probability(m, x_te)
    preds_te[name] = p
    acc = accuracy_score(y_te, p >= 0.5)
    ll = log_loss(y_te, p, labels=[0, 1])
    brier = brier_score_loss(y_te, p)
    print(f"BASELINE {name:25s} Acc: {acc*100:.2f}% | LogLoss: {ll:.4f} | Brier: {brier:.4f}")

# Nelder Mead baseline
x_val_tr, y_val_tr = x_base[tr_mask], y_base[tr_mask]
x_val, y_val = x_base[va_mask], y_base[va_mask]
val_preds = np.column_stack([probability(fit_symmetric(fact(), x_val_tr, y_val_tr), x_val) for fact in base_factories.values()])

def obj(w):
    w = np.maximum(w, 0)
    if w.sum() <= 1e-9: return 10.0
    w = w / w.sum()
    p = np.clip(val_preds @ w, 1e-6, 1 - 1e-6)
    return log_loss(y_val, p, labels=[0, 1])

opt_w = np.maximum(minimize(obj, np.ones(4)/4, method='Nelder-Mead').x, 0)
opt_w /= opt_w.sum()
p_te_base = np.column_stack(list(preds_te.values())) @ opt_w
acc_base = accuracy_score(y_te, p_te_base >= 0.5)
ll_base = log_loss(y_te, p_te_base, labels=[0, 1])
brier_base = brier_score_loss(y_te, p_te_base)
print(f"BASELINE Nelder-Mead Ensemble       Acc: {acc_base*100:.2f}% | LogLoss: {ll_base:.4f} | Brier: {brier_base:.4f}")
print("Baseline Weights:", {name: round(float(w), 3) for name, w in zip(base_factories.keys(), opt_w)})
