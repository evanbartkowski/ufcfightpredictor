"""MMA Cipher Advanced Feature Engineering & Multi-Model AI Ensemble Pipeline.

Strict zero-leakage guarantee: every historical feature is reconstructed solely
from information available prior to the bout date.
"""
import hashlib
import json
from collections import defaultdict
from datetime import date
from itertools import groupby

import numpy as np
from scipy.optimize import minimize
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
import lightgbm as lgb

from .store import identity, now

FEATURES = [
    'Elo',
    'Recorded wins',
    'Recorded losses',
    'Win streak',
    'Recent form',
    'KO win rate',
    'Submission win rate',
    'Experience',
    'Days since last fight',
    'Recent overperformance',
    'Smoothed win rate',
    'Finish loss rate',
    'Recent three form',
    'Log inactivity',
    'Age differential',
    'Height differential',
    'Reach differential',
    'Stance advantage',
    'Sig strike rate differential',
    'Sig strike accuracy differential',
    'Takedown rate differential',
    'Takedown accuracy differential',
    'Submission attempt differential',
    'Total rounds differential'
]
SCHEMA = 4


def fresh():
    """Default baseline career and physical state for an unrecorded fighter."""
    return dict(
        elo=1500., wins=0, losses=0, streak=0, recent=[], ko=0, sub=0, dec=0,
        last=None, residuals=[], finish_losses=0, total_rounds=0,
        age=30.0, height=175.0, reach=175.0, stance_val=0.0,
        sig_str_landed=3.5, sig_str_acc=0.45, td_landed=1.5, td_acc=0.40, sub_att=0.5
    )


def _safe_float(val, default):
    if val is None or val == '':
        return float(default)
    try:
        return float(val)
    except (ValueError, TypeError):
        return float(default)


def vector(state, day, attributes=None):
    """Generate a single fighter's pre-bout feature vector."""
    n = state['wins'] + state['losses']
    rest = min((date.fromisoformat(day) - date.fromisoformat(state['last'])).days, 1095) if state.get('last') else 365
    attrs = attributes or {}

    age = _safe_float(attrs.get('age'), state.get('age', 30.0))
    height = _safe_float(attrs.get('height'), state.get('height', 175.0))
    reach = _safe_float(attrs.get('reach'), state.get('reach', 175.0))
    stance = attrs.get('stance') or state.get('stance', 'Orthodox')
    st_val = 1.0 if 'southpaw' in str(stance).lower() else (0.5 if 'switch' in str(stance).lower() else 0.0)

    sig_str = _safe_float(attrs.get('sig_str_landed'), state.get('sig_str_landed', 3.5))
    sig_acc = _safe_float(attrs.get('sig_str_pct'), state.get('sig_str_acc', 0.45))
    td_land = _safe_float(attrs.get('td_landed'), state.get('td_landed', 1.5))
    td_pct = _safe_float(attrs.get('td_pct'), state.get('td_acc', 0.40))
    sub_att = _safe_float(attrs.get('sub_att'), state.get('sub_att', 0.5))
    total_rounds = float(state.get('total_rounds', 0))

    return np.array([
        state['elo'], state['wins'], state['losses'], state['streak'],
        (sum(state['recent']) + 1) / (len(state['recent']) + 2),
        state['ko'] / max(n, 1), state['sub'] / max(n, 1), n, rest,
        np.mean(state['residuals']) if state['residuals'] else 0.,
        (state['wins'] + 2) / (n + 4), state['finish_losses'] / (n + 4),
        (sum(state.get('recent3', state['recent'][-3:])) + 1) / (len(state.get('recent3', state['recent'][-3:])) + 2),
        np.log1p(rest),
        age, height, reach, st_val,
        sig_str, sig_acc, td_land, td_pct, sub_att, total_rounds
    ], dtype=float)


def features(states, red, blue, day, bout_meta=None):
    """Compute symmetric matchup differential features (Red minus Blue)."""
    meta = bout_meta or {}
    red_attrs = {
        'age': meta.get('red_age'),
        'height': meta.get('red_height'),
        'reach': meta.get('red_reach'),
        'stance': meta.get('red_stance'),
        'sig_str_landed': meta.get('red_sig_str_landed'),
        'sig_str_pct': meta.get('red_sig_str_pct'),
        'td_landed': meta.get('red_td_landed'),
        'td_pct': meta.get('red_td_pct'),
        'sub_att': meta.get('red_sub_att')
    }
    blue_attrs = {
        'age': meta.get('blue_age'),
        'height': meta.get('blue_height'),
        'reach': meta.get('blue_reach'),
        'stance': meta.get('blue_stance'),
        'sig_str_landed': meta.get('blue_sig_str_landed'),
        'sig_str_pct': meta.get('blue_sig_str_pct'),
        'td_landed': meta.get('blue_td_landed'),
        'td_pct': meta.get('blue_td_pct'),
        'sub_att': meta.get('blue_sub_att')
    }
    vec_red = vector(states.get(identity(red), fresh()), day, red_attrs)
    vec_blue = vector(states.get(identity(blue), fresh()), day, blue_attrs)
    return vec_red - vec_blue


def replay(fights):
    """Reconstruct fighter state chronologically prior to each calendar day."""
    states = defaultdict(fresh)
    examples = []
    ordered = sorted(fights, key=lambda f: (f['date'], f['id']))
    for day, group in groupby(ordered, key=lambda f: f['date']):
        group = list(group)
        # All bouts on the same day strictly use states from before the day began
        for f in group:
            if f.get('winner') in ('red', 'blue'):
                feat = features(states, f['red'], f['blue'], day, f)
                y = int(f['winner'] == 'red')
                examples.append((day, feat, y, f.get('method', '')))
        for f in group:
            if f.get('winner') not in ('red', 'blue'):
                continue
            a, b = states[identity(f['red'])], states[identity(f['blue'])]
            y = int(f['winner'] == 'red')
            expected = 1 / (1 + 10 ** ((b['elo'] - a['elo']) / 400))
            is_finish = any(m in f.get('method', '').upper() for m in ('KO', 'TKO', 'SUB'))
            k = 28. if is_finish else 20.
            delta = k * (y - expected)
            a['elo'] += delta
            b['elo'] -= delta
            for state, won, expectation, corner in [(a, y, expected, 'red'), (b, 1 - y, 1 - expected, 'blue')]:
                state['residuals'] = (state['residuals'] + [won - expectation])[-5:]
                state['finish_losses'] += int(not won and is_finish)
                state['wins'] += won
                state['losses'] += 1 - won
                state['streak'] = max(0, state['streak']) + 1 if won else min(0, state['streak']) - 1
                state['recent'] = (state['recent'] + [won])[-5:]
                state['recent3'] = (state.get('recent3', []) + [won])[-3:]
                state['ko'] += int(won and any(m in f.get('method', '').upper() for m in ('KO', 'TKO')))
                state['sub'] += int(won and 'SUB' in f.get('method', '').upper())
                state['last'] = day
                state['total_rounds'] += int(f.get('total_rounds') or 3)
                if f.get(corner + '_age') is not None: state['age'] = f[corner + '_age']
                if f.get(corner + '_height') is not None: state['height'] = f[corner + '_height']
                if f.get(corner + '_reach') is not None: state['reach'] = f[corner + '_reach']
                if f.get(corner + '_stance'): state['stance'] = f[corner + '_stance']
                if f.get(corner + '_sig_str_landed') is not None: state['sig_str_landed'] = f[corner + '_sig_str_landed']
                if f.get(corner + '_sig_str_pct') is not None: state['sig_str_acc'] = f[corner + '_sig_str_pct']
                if f.get(corner + '_td_landed') is not None: state['td_landed'] = f[corner + '_td_landed']
                if f.get(corner + '_td_pct') is not None: state['td_acc'] = f[corner + '_td_pct']
                if f.get(corner + '_sub_att') is not None: state['sub_att'] = f[corner + '_sub_att']

    # Preserve tuple format (day, feat, y) for backwards compatibility
    cleaned_examples = [(e[0], e[1], e[2]) for e in examples]
    return cleaned_examples, dict(states)


def replay_with_methods(fights):
    """Replay helper that also returns method categories for multi-task training."""
    states = defaultdict(fresh)
    examples = []
    ordered = sorted(fights, key=lambda f: (f['date'], f['id']))
    for day, group in groupby(ordered, key=lambda f: f['date']):
        group = list(group)
        for f in group:
            if f.get('winner') in ('red', 'blue'):
                feat = features(states, f['red'], f['blue'], day, f)
                y = int(f['winner'] == 'red')
                m_str = f.get('method', '').upper()
                if any(k in m_str for k in ('KO', 'TKO')):
                    m_cat = 1  # KO/TKO
                elif 'SUB' in m_str:
                    m_cat = 2  # Submission
                else:
                    m_cat = 0  # Decision / Other
                examples.append((day, feat, y, m_cat))
        for f in group:
            if f.get('winner') not in ('red', 'blue'):
                continue
            a, b = states[identity(f['red'])], states[identity(f['blue'])]
            y = int(f['winner'] == 'red')
            expected = 1 / (1 + 10 ** ((b['elo'] - a['elo']) / 400))
            is_finish = any(m in f.get('method', '').upper() for m in ('KO', 'TKO', 'SUB'))
            k = 28. if is_finish else 20.
            delta = k * (y - expected)
            a['elo'] += delta
            b['elo'] -= delta
            for state, won, expectation, corner in [(a, y, expected, 'red'), (b, 1 - y, 1 - expected, 'blue')]:
                state['residuals'] = (state['residuals'] + [won - expectation])[-5:]
                state['finish_losses'] += int(not won and is_finish)
                state['wins'] += won
                state['losses'] += 1 - won
                state['streak'] = max(0, state['streak']) + 1 if won else min(0, state['streak']) - 1
                state['recent'] = (state['recent'] + [won])[-5:]
                state['recent3'] = (state.get('recent3', []) + [won])[-3:]
                state['ko'] += int(won and any(m in f.get('method', '').upper() for m in ('KO', 'TKO')))
                state['sub'] += int(won and 'SUB' in f.get('method', '').upper())
                state['last'] = day
                state['total_rounds'] += int(f.get('total_rounds') or 3)
                if f.get(corner + '_age') is not None: state['age'] = f[corner + '_age']
                if f.get(corner + '_height') is not None: state['height'] = f[corner + '_height']
                if f.get(corner + '_reach') is not None: state['reach'] = f[corner + '_reach']
                if f.get(corner + '_stance'): state['stance'] = f[corner + '_stance']
                if f.get(corner + '_sig_str_landed') is not None: state['sig_str_landed'] = f[corner + '_sig_str_landed']
                if f.get(corner + '_sig_str_pct') is not None: state['sig_str_acc'] = f[corner + '_sig_str_pct']
                if f.get(corner + '_td_landed') is not None: state['td_landed'] = f[corner + '_td_landed']
                if f.get(corner + '_td_pct') is not None: state['td_acc'] = f[corner + '_td_pct']
                if f.get(corner + '_sub_att') is not None: state['sub_att'] = f[corner + '_sub_att']
    return examples, dict(states)


def probability(model, x):
    """Enforce exact corner swap symmetry for any binary estimator."""
    x = np.asarray(x).reshape(-1, len(FEATURES))
    return (model.predict_proba(x)[:, 1] + 1 - model.predict_proba(-x)[:, 1]) / 2


def fit_symmetric(model, x, y):
    """Train with symmetric parity augmentation."""
    return model.fit(np.concatenate([x, -x]), np.concatenate([y, 1 - y]))


def fit(model, x, y):
    return fit_symmetric(model, x, y)


def make_ensemble_factories():
    """Diverse machine learning models for the MMA Cipher ensemble."""
    return {
        'MMA Cipher Logistic Regression': lambda: make_pipeline(
            StandardScaler(),
            LogisticRegression(C=0.01, max_iter=1000, random_state=42)
        ),
        'MMA Cipher HistGradientBoosting': lambda: HistGradientBoostingClassifier(
            max_iter=100, max_leaf_nodes=8, learning_rate=0.03, l2_regularization=20.0, random_state=42
        ),
        'MMA Cipher LightGBM': lambda: lgb.LGBMClassifier(
            n_estimators=90, num_leaves=8, learning_rate=0.03, reg_lambda=20.0, verbose=-1, random_state=42
        ),
        'MMA Cipher Random Forest': lambda: RandomForestClassifier(
            n_estimators=180, max_depth=5, min_samples_leaf=20, random_state=42
        )
    }


def train(fights):
    """Train the MMA Cipher multi-model ensemble with chronological validation and holdout scoring."""
    canonical = []
    for f in fights:
        if identity(f['red']) > identity(f['blue']):
            f = dict(f, red=f['blue'], blue=f['red'],
                     winner={'red': 'blue', 'blue': 'red'}.get(f['winner'], f['winner']),
                     red_age=f.get('blue_age'), blue_age=f.get('red_age'),
                     red_height=f.get('blue_height'), blue_height=f.get('red_height'),
                     red_reach=f.get('blue_reach'), blue_reach=f.get('red_reach'),
                     red_stance=f.get('blue_stance'), blue_stance=f.get('red_stance'),
                     red_sig_str_landed=f.get('blue_sig_str_landed'), blue_sig_str_landed=f.get('red_sig_str_landed'),
                     red_sig_str_pct=f.get('blue_sig_str_pct'), blue_sig_str_pct=f.get('red_sig_str_pct'),
                     red_td_landed=f.get('blue_td_landed'), blue_td_landed=f.get('red_td_landed'),
                     red_td_pct=f.get('blue_td_pct'), blue_td_pct=f.get('red_td_pct'),
                     red_sub_att=f.get('blue_sub_att'), blue_sub_att=f.get('red_sub_att'))
        canonical.append(f)

    examples_with_method, states = replay_with_methods(canonical)
    days = sorted({e[0] for e in examples_with_method})
    if len(days) < 10:
        raise ValueError('At least 10 event dates are required for chronological evaluation.')

    val_start, test_start = days[int(len(days) * .65)], days[int(len(days) * .8)]
    x = np.array([e[1] for e in examples_with_method])
    y = np.array([e[2] for e in examples_with_method])
    m_targets = np.array([e[3] for e in examples_with_method])
    dates = np.array([e[0] for e in examples_with_method])

    tr = dates < val_start
    va = (dates >= val_start) & (dates < test_start)
    te = dates >= test_start

    factories = make_ensemble_factories()
    folds = [(.45, .55), (.55, .65), (.65, .8)]
    validation_log_loss, validation_fold_log_loss = {}, {}

    for name, factory in factories.items():
        losses = []
        for start, end in folds:
            train_mask = dates < days[int(len(days) * start)]
            val_mask = (dates >= days[int(len(days) * start)]) & (dates < days[int(len(days) * end)])
            cand = fit_symmetric(factory(), x[train_mask], y[train_mask])
            p_val = probability(cand, x[val_mask])
            losses.append(float(log_loss(y[val_mask], p_val, labels=[0, 1])))
        validation_fold_log_loss[name] = losses
        validation_log_loss[name] = float(np.mean(losses))

    # Evaluate on the validation window to optimize ensemble blend weights
    x_tr_sym = np.concatenate([x[tr], -x[tr]])
    y_tr_sym = np.concatenate([y[tr], 1 - y[tr]])
    fitted_val = {name: factory().fit(x_tr_sym, y_tr_sym) for name, factory in factories.items()}
    val_preds = np.column_stack([probability(fitted_val[name], x[va]) for name in factories])

    def objective(w):
        w = np.maximum(w, 0)
        s = w.sum()
        if s <= 1e-9: return 10.0
        w = w / s
        p_blend = np.clip(val_preds @ w, 1e-6, 1 - 1e-6)
        return log_loss(y[va], p_blend, labels=[0, 1])

    init_w = np.ones(len(factories)) / len(factories)
    opt_res = minimize(objective, init_w, method='Nelder-Mead')
    opt_w = np.maximum(opt_res.x, 0)
    if opt_w.sum() > 0:
        opt_w /= opt_w.sum()
    else:
        opt_w = init_w
    ensemble_weights = {name: float(w) for name, w in zip(factories.keys(), opt_w)}

    # Train serving models on all data before holdout for evaluation
    x_pretest_sym = np.concatenate([x[~te], -x[~te]])
    y_pretest_sym = np.concatenate([y[~te], 1 - y[~te]])
    eval_models = {name: factory().fit(x_pretest_sym, y_pretest_sym) for name, factory in factories.items()}
    te_preds = np.column_stack([probability(eval_models[name], x[te]) for name in factories])
    p_te_ensemble = te_preds @ opt_w

    elo_te = 1 / (1 + 10 ** (-x[te, 0] / 400))

    # Candidate model holdout metrics
    candidate_metrics = {}
    for i, name in enumerate(factories.keys()):
        cand_p = te_preds[:, i]
        candidate_metrics[name] = {
            'accuracy': float(accuracy_score(y[te], cand_p >= .5)),
            'log_loss': float(log_loss(y[te], cand_p, labels=[0, 1])),
            'brier': float(brier_score_loss(y[te], cand_p)),
            'weight': float(ensemble_weights.get(name, 0.0))
        }

    # Empirical calibration bins on chronological holdout
    test_indices = np.where(te)[0]
    p_te_clipped = np.clip(p_te_ensemble, 1e-4, 1.0 - 1e-4)
    confidences = np.maximum(p_te_clipped, 1.0 - p_te_clipped)
    corrects = (p_te_clipped >= 0.5) == (y[te] == 1)

    bins = [(0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 0.90), (0.90, 1.00)]
    calibration = []
    for low, high in bins:
        mask = (confidences >= low) & (confidences < high if high < 1.0 else confidences <= high)
        cnt = int(mask.sum())
        if cnt > 0:
            exp_val = float(confidences[mask].mean())
            obs_val = float(corrects[mask].mean())
            n_corr = int(corrects[mask].sum())
        else:
            exp_val = (low + high) / 2
            obs_val = 0.0
            n_corr = 0
        calibration.append({
            'range': f"{int(low*100)}–{int(high*100)}%",
            'expected': round(exp_val, 3),
            'observed': round(obs_val, 3),
            'count': cnt,
            'correct': n_corr
        })

    # Confidence distribution
    total_test_fights = max(len(test_indices), 1)
    confidence_distribution = [
        {
            'range': b['range'],
            'count': b['count'],
            'percentage': round((b['count'] / total_test_fights) * 100, 1)
        }
        for b in calibration
    ]

    # Holdout events history & accuracy over time
    test_bouts = []
    for idx_local, idx_global in enumerate(test_indices):
        d, feat, y_true, m_cat = examples_with_method[idx_global]
        orig_f = canonical[idx_global]
        p_val = float(p_te_clipped[idx_local])
        is_corr = int((p_val >= 0.5) == (y_true == 1))
        test_bouts.append({
            'date': d,
            'event': orig_f.get('event', d),
            'weight_class': orig_f.get('weight_class', 'Unknown'),
            'correct': is_corr,
            'confidence': float(confidences[idx_local])
        })

    events_history = []
    running_correct = 0
    running_total = 0
    for ev_idx, (ev_date, b_group) in enumerate(groupby(test_bouts, key=lambda b: b['date'])):
        b_list = list(b_group)
        n_ev = len(b_list)
        c_ev = sum(b['correct'] for b in b_list)
        running_correct += c_ev
        running_total += n_ev
        events_history.append({
            'index': ev_idx + 1,
            'date': ev_date,
            'event': b_list[0]['event'],
            'fights': n_ev,
            'correct': c_ev,
            'accuracy': round(c_ev / n_ev, 3) if n_ev else 0.0,
            'cumulative_accuracy': round(running_correct / running_total, 3),
            'cumulative_fights': running_total
        })

    # Weight class accuracy
    wc_groups = defaultdict(lambda: [0, 0])
    for b in test_bouts:
        wc = b['weight_class']
        wc_groups[wc][0] += b['correct']
        wc_groups[wc][1] += 1
    weight_class_stats = {
        wc: {'accuracy': round(corr / tot, 3), 'fights': tot, 'correct': corr}
        for wc, (corr, tot) in sorted(wc_groups.items(), key=lambda t: t[1][1], reverse=True)
        if tot >= 10
    }

    # Feature importances from regularized logistic pipeline
    lr_for_importance = eval_models.get('MMA Cipher Logistic Regression')
    feature_importances = []
    if lr_for_importance and hasattr(lr_for_importance, 'named_steps'):
        coefs = lr_for_importance.named_steps['logisticregression'].coef_[0]
        scales = lr_for_importance.named_steps['standardscaler'].scale_
        for f_name, c_val, s_val in zip(FEATURES, coefs, scales):
            feature_importances.append({
                'feature': f_name,
                'importance': round(float(abs(c_val)), 4),
                'direction': 'positive' if c_val > 0 else 'negative',
                'scale': round(float(s_val), 2)
            })
        feature_importances.sort(key=lambda item: item['importance'], reverse=True)

    metrics = dict(
        accuracy=float(accuracy_score(y[te], p_te_ensemble >= .5)),
        brier=float(brier_score_loss(y[te], p_te_ensemble)),
        log_loss=float(log_loss(y[te], p_te_ensemble, labels=[0, 1])),
        elo_accuracy=float(accuracy_score(y[te], elo_te >= .5)),
        majority_accuracy=float(accuracy_score(y[te], np.full(te.sum(), int(np.mean(y[~te]) >= .5)))),
        test_fights=int(te.sum()),
        test_start=test_start,
        test_end=days[-1],
        training_fights=len(examples_with_method),
        validation_log_loss=validation_log_loss,
        validation_fold_log_loss=validation_fold_log_loss,
        validation_scheme='Three expanding chronological windows; final 20% holdout',
        ensemble_weights=ensemble_weights,
        candidate_holdout_accuracies={name: float(accuracy_score(y[te], te_preds[:, i] >= .5))
                                      for i, name in enumerate(factories.keys())},
        candidate_metrics=candidate_metrics,
        calibration=calibration,
        confidence_distribution=confidence_distribution,
        events_history=events_history,
        weight_class_stats=weight_class_stats,
        feature_importances=feature_importances
    )

    # Train production models on the complete dataset
    x_full_sym = np.concatenate([x, -x])
    y_full_sym = np.concatenate([y, 1 - y])
    production_models = {name: factory().fit(x_full_sym, y_full_sym) for name, factory in factories.items()}

    # Train Method of Victory Classifier (Decision vs KO/TKO vs Submission)
    method_model = None
    if len(np.unique(m_targets)) >= 2:
        try:
            method_model = make_pipeline(
                StandardScaler(),
                LogisticRegression(C=0.05, max_iter=1000, random_state=42)
            )
            method_model.fit(x, m_targets)
        except Exception:
            method_model = None

    # Primary representative model (best individual validation model or LR)
    best_candidate_name = min(validation_log_loss, key=validation_log_loss.get)
    primary_model = production_models[best_candidate_name]

    version = hashlib.sha256((str(SCHEMA) + json.dumps(sorted(fights, key=lambda f: f['id']), sort_keys=True)).encode()).hexdigest()[:12]

    return dict(
        model=primary_model,
        models=production_models,
        weights=ensemble_weights,
        method_model=method_model,
        states=states,
        metrics=metrics,
        name='MMA Cipher Advanced Ensemble',
        version=version,
        schema=SCHEMA,
        trained_at=now(),
        through=days[-1]
    )


def _format_advantage(name, diff, is_red_pick):
    d = diff if is_red_pick else -diff
    if name == 'Elo':
        return f"Opponent-adjusted Elo rating edge (+{abs(d):.0f} rating points)"
    elif name == 'Age differential':
        return f"Youth & prime advantage ({abs(d):.1f} years younger)"
    elif name == 'Sig strike rate differential':
        return f"Striking volume rate advantage (+{abs(d):.1f} SLpM)"
    elif name == 'Sig strike accuracy differential':
        return f"Striking accuracy precision edge (+{abs(d)*100:.1f}%)"
    elif name == 'Takedown rate differential':
        return f"Offensive takedown volume advantage (+{abs(d):.1f} TD / 15m)"
    elif name == 'Takedown accuracy differential':
        return f"Takedown accuracy completion edge (+{abs(d)*100:.1f}%)"
    elif name == 'Submission attempt differential':
        return f"Submission grappling threat (+{abs(d):.1f} sub att / 15m)"
    elif name == 'Reach differential':
        return f"Reach wingspan advantage (+{abs(d):.1f} cm)"
    elif name == 'Height differential':
        return f"Height leverage advantage (+{abs(d):.1f} cm)"
    elif name == 'Win streak':
        return f"Momentum & active win streak (+{abs(d):.0f} consecutive wins)"
    elif name == 'Total rounds differential':
        return f"Octagon cage time experience (+{abs(d):.0f} rounds logged)"
    elif name == 'KO win rate':
        return f"Knockout finishing power (+{abs(d)*100:.1f}%)"
    elif name == 'Submission win rate':
        return f"Submission finishing threat (+{abs(d)*100:.1f}%)"
    elif name == 'Finish loss rate':
        return "Durability profile and finish resistance"
    elif name == 'Stance advantage':
        return "Stance matchup angle advantage (Southpaw / Switch)"
    elif name == 'Recent three form':
        return "Recent three-bout victory momentum"
    elif name == 'Recorded wins':
        return f"Professional experience depth (+{abs(d):.0f} recorded wins)"
    return f"Statistical advantage in {name.lower()}"


def _format_risk(name, diff, is_red_pick):
    d = -diff if is_red_pick else diff
    if name == 'Age differential':
        return f"Opponent holds a youth advantage ({abs(d):.1f} years younger)"
    elif name == 'Reach differential':
        return f"Opponent has longer reach (+{abs(d):.1f} cm wingspan)"
    elif name == 'Height differential':
        return f"Opponent is taller (+{abs(d):.1f} cm)"
    elif name == 'Sig strike rate differential':
        return f"Opponent lands higher striking output (+{abs(d):.1f} SLpM)"
    elif name == 'Takedown rate differential':
        return f"Opponent completes more takedowns (+{abs(d):.1f} TD / 15m)"
    elif name == 'Submission attempt differential':
        return f"Opponent is dangerous on the ground (+{abs(d):.1f} sub att / 15m)"
    elif name == 'Total rounds differential':
        return f"Opponent has more cage time experience (+{abs(d):.0f} rounds)"
    return f"Opponent statistical threat in {name.lower()}"


def _build_radar(fighter_state, attrs):
    slpm = _safe_float(attrs.get('sig_str_landed'), fighter_state.get('sig_str_landed', 3.5))
    acc = _safe_float(attrs.get('sig_str_pct'), fighter_state.get('sig_str_acc', 0.45))
    td = _safe_float(attrs.get('td_landed'), fighter_state.get('td_landed', 1.5))
    td_def = _safe_float(attrs.get('td_def'), 0.65)
    sub = _safe_float(attrs.get('sub_att'), fighter_state.get('sub_att', 0.5))
    exp = min(1.0, (fighter_state.get('wins', 0) + fighter_state.get('losses', 0)) / 22.0)
    return {
        'striking_volume': round(min(100.0, max(15.0, (slpm / 7.0) * 100)), 1),
        'striking_accuracy': round(min(100.0, max(20.0, (acc / 0.65) * 100)), 1),
        'takedown_volume': round(min(100.0, max(10.0, (td / 4.0) * 100)), 1),
        'takedown_defense': round(min(100.0, max(20.0, (td_def / 0.85) * 100)), 1),
        'submission_threat': round(min(100.0, max(10.0, (sub / 2.5) * 100)), 1),
        'octagon_experience': round(min(100.0, max(15.0, exp * 100)), 1),
    }


def predict(bundle, red, blue, day, bout_meta=None):
    """Predict matchup win probability, method of victory, and ensemble consensus."""
    x = features(bundle['states'], red, blue, day, bout_meta)

    # Ensemble model predictions
    models_dict = bundle.get('models', {})
    weights_dict = bundle.get('weights', {})
    ensemble_breakdown = {}
    weighted_p = 0.0

    if models_dict and weights_dict:
        total_w = sum(weights_dict.values()) or 1.0
        for name, m in models_dict.items():
            p_m = float(probability(m, x)[0])
            w = weights_dict.get(name, 0.0) / total_w
            weighted_p += w * p_m
            ensemble_breakdown[name] = {
                'red_probability': p_m,
                'blue_probability': 1.0 - p_m,
                'weight': round(w, 3)
            }
        p = float(np.clip(weighted_p, 0.005, 0.995))
    else:
        # Fallback to single primary model
        p = float(probability(bundle['model'], x)[0])

    # Method of victory prediction
    method_probs = {'decision': 0.50, 'ko': 0.32, 'submission': 0.18}
    likely_method = 'Decision'
    if 'method_model' in bundle and bundle['method_model'] is not None:
        try:
            m_probs = bundle['method_model'].predict_proba(x.reshape(1, -1))[0]
            method_probs = {
                'decision': round(float(m_probs[0]), 3),
                'ko': round(float(m_probs[1]), 3),
                'submission': round(float(m_probs[2]), 3)
            }
            method_names = ['Decision', 'KO/TKO', 'Submission']
            likely_method = method_names[int(np.argmax(m_probs))]
        except Exception:
            pass

    a = bundle['states'].get(identity(red), fresh())
    b = bundle['states'].get(identity(blue), fresh())

    tape = {
        'age': {'red': a.get('age', 30.0), 'blue': b.get('age', 30.0), 'diff': float(x[14]) if len(x) > 14 else 0.0},
        'height': {'red': a.get('height', 175.0), 'blue': b.get('height', 175.0), 'diff': float(x[15]) if len(x) > 15 else 0.0},
        'reach': {'red': a.get('reach', 175.0), 'blue': b.get('reach', 175.0), 'diff': float(x[16]) if len(x) > 16 else 0.0},
        'stance': {'red': a.get('stance', 'Orthodox'), 'blue': b.get('stance', 'Orthodox')}
    }

    pick = red if p >= .5 else blue
    is_red_pick = (pick == red)
    confidence = max(p, 1.0 - p)

    # Feature attribution explanation engine
    advantages = []
    risks = []
    lr_prod = models_dict.get('MMA Cipher Logistic Regression')
    if lr_prod and hasattr(lr_prod, 'named_steps'):
        coefs = lr_prod.named_steps['logisticregression'].coef_[0]
        scales = lr_prod.named_steps['standardscaler'].scale_
        contributions = (x / scales) * coefs
        items = []
        for f_name, diff_val, c_val in zip(FEATURES, x, contributions):
            # Signed contribution toward the picked fighter
            pick_contribution = c_val if is_red_pick else -c_val
            items.append({
                'feature': f_name,
                'diff': float(diff_val),
                'contribution': float(pick_contribution)
            })

        # Top advantages favoring the pick
        items.sort(key=lambda it: it['contribution'], reverse=True)
        for it in items[:4]:
            if it['contribution'] > 0.01:
                advantages.append({
                    'title': _format_advantage(it['feature'], it['diff'], is_red_pick),
                    'feature': it['feature'],
                    'diff': round(it['diff'], 2),
                    'impact': 'high' if it['contribution'] > 0.25 else 'medium'
                })

        # Top risk factors / vulnerabilities favoring opponent
        for it in reversed(items):
            if it['contribution'] < -0.01 and len(risks) < 2:
                risks.append({
                    'title': _format_risk(it['feature'], it['diff'], is_red_pick),
                    'feature': it['feature'],
                    'diff': round(it['diff'], 2),
                    'impact': 'high' if it['contribution'] < -0.2 else 'moderate'
                })

    adv_summary = f"{pick} is favored primarily by " + (", ".join(a['title'].split('(')[0].strip() for a in advantages[:2]) if advantages else "overall algorithmic rating") + "."
    if risks:
        adv_summary += f" Key matchup risk: {risks[0]['title'].split('(')[0].strip()}."

    explanation = {
        'pick': pick,
        'summary': adv_summary,
        'advantages': advantages,
        'risks': risks
    }

    # Model consensus metrics
    model_count = len(ensemble_breakdown)
    agreed_count = sum(1 for m in ensemble_breakdown.values() if (m['red_probability'] >= 0.5) == is_red_pick) if model_count else 1
    agreement_pct = round(agreed_count / max(model_count, 1), 2)
    probs_list = [m['red_probability'] for m in ensemble_breakdown.values()] if model_count else [p]
    spread = round(max(probs_list) - min(probs_list), 3) if probs_list else 0.0
    uncertainty = 'Low' if spread < 0.12 else ('Moderate' if spread < 0.22 else 'Elevated')
    consensus_tier = f"Unanimous ({agreed_count}/{model_count})" if agreed_count == model_count else f"Majority ({agreed_count}/{model_count})" if agreed_count > model_count / 2 else "Split Consensus"

    consensus = {
        'agreement_count': agreed_count,
        'total_models': model_count,
        'agreement_pct': agreement_pct,
        'tier': consensus_tier,
        'spread': spread,
        'uncertainty': uncertainty
    }

    # Fighter radar telemetry
    meta_dict = bout_meta or {}
    radar = {
        'red': _build_radar(a, {
            'sig_str_landed': meta_dict.get('red_sig_str_landed'),
            'sig_str_pct': meta_dict.get('red_sig_str_pct'),
            'td_landed': meta_dict.get('red_td_landed'),
            'td_def': meta_dict.get('red_td_def'),
            'sub_att': meta_dict.get('red_sub_att')
        }),
        'blue': _build_radar(b, {
            'sig_str_landed': meta_dict.get('blue_sig_str_landed'),
            'sig_str_pct': meta_dict.get('blue_sig_str_pct'),
            'td_landed': meta_dict.get('blue_td_landed'),
            'td_def': meta_dict.get('blue_td_def'),
            'sub_att': meta_dict.get('blue_sub_att')
        })
    }

    # Upset potential evaluation
    upset_signals = []
    # If the underdog pick has high stylistic edge
    if confidence >= 0.52 and confidence <= 0.65:
        if any('takedown' in a['feature'].lower() for a in advantages) and any('strike' in r['feature'].lower() for r in risks):
            upset_signals.append("Stylistic clash: Grappler vs Striker dynamic")
    if uncertainty == 'Elevated':
        upset_signals.append("Elevated model divergence: Non-linear tree models diverge from linear baseline")

    upset_radar = {
        'is_candidate': len(upset_signals) > 0 or (confidence >= 0.52 and confidence <= 0.64 and spread >= 0.10),
        'signals': upset_signals
    }

    return dict(
        red_probability=p,
        blue_probability=1.0 - p,
        pick=pick,
        confidence=confidence,
        model_version=bundle['version'],
        model_name=bundle.get('name', 'MMA Cipher Ensemble'),
        models=ensemble_breakdown,
        consensus=consensus,
        explanation=explanation,
        radar=radar,
        upset_radar=upset_radar,
        method_probabilities=method_probs,
        likely_method=likely_method,
        tale_of_the_tape=tape,
        features=x.tolist(),
        feature_names=FEATURES,
        low_history=min(a['wins'] + a['losses'], b['wins'] + b['losses']) < 3,
        red_history=a,
        blue_history=b
    )
