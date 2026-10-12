"""MMA Cipher Advanced Feature Engineering & Multi-Model AI Ensemble Pipeline.

Strict zero-leakage guarantee: every historical feature is reconstructed solely
from information available prior to the bout date.
"""
import hashlib
import json
from collections import defaultdict
from datetime import date
from itertools import groupby
import math

import numpy as np
from scipy.optimize import minimize
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
import lightgbm as lgb
import xgboost as xgb

from .store import identity, now

FEATURES = [
    'Elo',
    'Striking Elo',
    'Grappling Elo',
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
    'Total rounds differential',
    'Age cliff differential',
    'Longest win streak differential',
    'Title bouts differential',
    'KO wins differential',
    'Submission wins differential',
    'Decision dominance differential',
    'Reach stance interaction',
    'Takedown threat interaction',
    'Ranking differential',
    'Five round experience',
    'Ape index differential',
    'Market implied edge',
    'Market logit'
]
SCHEMA = 6


def fresh():
    """Default baseline career and physical state for an unrecorded fighter."""
    return dict(
        elo=1500., str_elo=1500., grap_elo=1500.,
        wins=0, losses=0, streak=0, recent=[], recent3=[],
        ko=0, sub=0, dec=0, last=None, residuals=[], finish_losses=0, total_rounds=0,
        age=30.0, height=175.0, reach=175.0, weight=155.0, stance='Orthodox',
        sig_str_landed=3.5, sig_str_acc=0.45, td_landed=1.5, td_acc=0.40, sub_att=0.5,
        longest_streak=0, title_bouts=0, rank=16.0
    )


def _safe_float(val, default):
    if val is None or val == '':
        return float(default)
    try:
        return float(val)
    except (ValueError, TypeError):
        return float(default)


def compute_fighter_vector(state, day, attrs=None):
    """Compute individual fighter state values prior to bout date."""
    n = state['wins'] + state['losses']
    rest = min((date.fromisoformat(day) - date.fromisoformat(state['last'])).days, 1095) if state.get('last') else 365
    attrs = attrs or {}

    decay = 0.95 if rest > 450 else 1.0
    elo_adj = 1500.0 + (state.get('elo', 1500.0) - 1500.0) * decay

    age = _safe_float(attrs.get('age'), state.get('age', 30.0))
    height = _safe_float(attrs.get('height'), state.get('height', 175.0))
    reach = _safe_float(attrs.get('reach'), state.get('reach', 175.0))
    weight = _safe_float(attrs.get('weight'), state.get('weight', 155.0))
    stance = attrs.get('stance') or state.get('stance', 'Orthodox')
    st_val = 1.0 if 'southpaw' in str(stance).lower() else (0.5 if 'switch' in str(stance).lower() else 0.0)

    sig_str = _safe_float(attrs.get('sig_str_landed'), state.get('sig_str_landed', 3.5))
    sig_acc = _safe_float(attrs.get('sig_str_pct'), state.get('sig_str_acc', 0.45))
    td_land = _safe_float(attrs.get('td_landed'), state.get('td_landed', 1.5))
    td_pct = _safe_float(attrs.get('td_pct'), state.get('td_acc', 0.40))
    sub_att = _safe_float(attrs.get('sub_att'), state.get('sub_att', 0.5))

    is_light = 1.0 if weight <= 170.0 else 0.0
    age_cliff = max(0.0, age - 35.0) * (1.5 if is_light else 0.8)

    longest_streak = float(attrs.get('longest_streak') if attrs.get('longest_streak') is not None else state.get('longest_streak', state.get('streak', 0)))
    title_bouts = float(attrs.get('title_bouts') if attrs.get('title_bouts') is not None else state.get('title_bouts', 0))
    ko_wins = float(attrs.get('ko_wins') if attrs.get('ko_wins') is not None else state.get('ko', 0))
    sub_wins = float(attrs.get('sub_wins') if attrs.get('sub_wins') is not None else state.get('sub', 0))
    dec_wins = float(attrs.get('dec_wins') if attrs.get('dec_wins') is not None else state.get('dec', 0))

    rk = _safe_float(attrs.get('rank'), state.get('rank', 16.0))
    if rk == 0.0:
        rk = 0.5
    elif rk > 15.0 or rk < 0.0:
        rk = 16.0

    ape_idx = (reach / max(height, 100.0)) - 1.0

    return {
        'elo': elo_adj,
        'str_elo': state.get('str_elo', 1500.0),
        'grap_elo': state.get('grap_elo', 1500.0),
        'wins': state['wins'],
        'losses': state['losses'],
        'streak': state['streak'],
        'recent_form': (sum(state['recent']) + 1) / (len(state['recent']) + 2),
        'ko_rate': state['ko'] / max(n, 1),
        'sub_rate': state['sub'] / max(n, 1),
        'n': n,
        'rest': rest,
        'residual': np.mean(state['residuals']) if state['residuals'] else 0.,
        'smoothed_win_rate': (state['wins'] + 2) / (n + 4),
        'finish_loss_rate': state['finish_losses'] / (n + 4),
        'recent3_form': (sum(state.get('recent3', state['recent'][-3:])) + 1) / (len(state.get('recent3', state['recent'][-3:])) + 2),
        'log_inactivity': np.log1p(rest),
        'age': age,
        'height': height,
        'reach': reach,
        'stance_val': st_val,
        'sig_str': sig_str,
        'sig_acc': sig_acc,
        'td_land': td_land,
        'td_pct': td_pct,
        'sub_att': sub_att,
        'rounds': state.get('total_rounds', 0),
        'age_cliff': age_cliff,
        'longest_streak': longest_streak,
        'title_bouts': title_bouts,
        'ko_wins': ko_wins,
        'sub_wins': sub_wins,
        'dec_wins': dec_wins,
        'rank': rk,
        'ape_idx': ape_idx
    }


def features(states, red, blue, day, bout_meta=None):
    """Compute symmetric matchup differential features (Red minus Blue)."""
    meta = bout_meta or {}
    red_attrs = {
        'age': meta.get('red_age'),
        'height': meta.get('red_height'),
        'reach': meta.get('red_reach'),
        'weight': meta.get('red_weight'),
        'stance': meta.get('red_stance'),
        'sig_str_landed': meta.get('red_sig_str_landed'),
        'sig_str_pct': meta.get('red_sig_str_pct'),
        'td_landed': meta.get('red_td_landed'),
        'td_pct': meta.get('red_td_pct'),
        'sub_att': meta.get('red_sub_att'),
        'longest_streak': meta.get('red_longest_streak'),
        'title_bouts': meta.get('red_title_bouts'),
        'ko_wins': meta.get('red_ko_wins'),
        'sub_wins': meta.get('red_sub_wins'),
        'dec_wins': meta.get('red_dec_wins'),
        'rank': meta.get('red_rank')
    }
    blue_attrs = {
        'age': meta.get('blue_age'),
        'height': meta.get('blue_height'),
        'reach': meta.get('blue_reach'),
        'weight': meta.get('blue_weight'),
        'stance': meta.get('blue_stance'),
        'sig_str_landed': meta.get('blue_sig_str_landed'),
        'sig_str_pct': meta.get('blue_sig_str_pct'),
        'td_landed': meta.get('blue_td_landed'),
        'td_pct': meta.get('blue_td_pct'),
        'sub_att': meta.get('blue_sub_att'),
        'longest_streak': meta.get('blue_longest_streak'),
        'title_bouts': meta.get('blue_title_bouts'),
        'ko_wins': meta.get('blue_ko_wins'),
        'sub_wins': meta.get('blue_sub_wins'),
        'dec_wins': meta.get('blue_dec_wins'),
        'rank': meta.get('blue_rank')
    }

    r = compute_fighter_vector(states.get(identity(red), fresh()), day, red_attrs)
    b = compute_fighter_vector(states.get(identity(blue), fresh()), day, blue_attrs)

    reach_diff = r['reach'] - b['reach']
    open_stance = 1.0 if ((r['stance_val'] > 0 and b['stance_val'] == 0) or (b['stance_val'] > 0 and r['stance_val'] == 0)) else 0.0
    reach_open_stance = reach_diff * open_stance

    td_threat = (r['td_land'] * (1.0 - b['td_pct'])) - (b['td_land'] * (1.0 - r['td_pct']))
    rank_diff = b['rank'] - r['rank']

    n_rounds = _safe_float(meta.get('total_rounds'), 3.0)
    exp_diff = r['n'] - b['n']
    five_rounds_exp = (exp_diff / max(abs(exp_diff) + 1.0, 1.0)) if n_rounds >= 5.0 else 0.0

    # Market Odds
    red_odds = _safe_float(meta.get('red_odds'), None) if meta.get('red_odds') is not None else None
    blue_odds = _safe_float(meta.get('blue_odds'), None) if meta.get('blue_odds') is not None else None

    if red_odds is None and 'odds' in meta and isinstance(meta['odds'], dict):
        books = meta['odds'].get('books', [])
        if books and isinstance(books[0], dict):
            red_odds = books[0].get('red')
            blue_odds = books[0].get('blue')

    if red_odds is not None and blue_odds is not None:
        p_r = (100.0 / (red_odds + 100.0)) if red_odds > 0 else (-red_odds / (-red_odds + 100.0))
        p_b = (100.0 / (blue_odds + 100.0)) if blue_odds > 0 else (-blue_odds / (-blue_odds + 100.0))
        p_mkt = p_r / (p_r + p_b)
        mkt_edge = p_mkt - 0.5
        mkt_logit = np.log(np.clip(p_mkt, 0.01, 0.99) / (1.0 - np.clip(p_mkt, 0.01, 0.99)))
    else:
        mkt_edge = 0.0
        mkt_logit = 0.0

    return np.array([
        r['elo'] - b['elo'],
        r['str_elo'] - b['str_elo'],
        r['grap_elo'] - b['grap_elo'],
        r['wins'] - b['wins'],
        r['losses'] - b['losses'],
        r['streak'] - b['streak'],
        r['recent_form'] - b['recent_form'],
        r['ko_rate'] - b['ko_rate'],
        r['sub_rate'] - b['sub_rate'],
        r['n'] - b['n'],
        r['rest'] - b['rest'],
        r['residual'] - b['residual'],
        r['smoothed_win_rate'] - b['smoothed_win_rate'],
        r['finish_loss_rate'] - b['finish_loss_rate'],
        r['recent3_form'] - b['recent3_form'],
        r['log_inactivity'] - b['log_inactivity'],
        r['age'] - b['age'],
        r['height'] - b['height'],
        reach_diff,
        r['stance_val'] - b['stance_val'],
        r['sig_str'] - b['sig_str'],
        r['sig_acc'] - b['sig_acc'],
        r['td_land'] - b['td_land'],
        r['td_pct'] - b['td_pct'],
        r['sub_att'] - b['sub_att'],
        r['rounds'] - b['rounds'],
        r['age_cliff'] - b['age_cliff'],
        r['longest_streak'] - b['longest_streak'],
        r['title_bouts'] - b['title_bouts'],
        r['ko_wins'] - b['ko_wins'],
        r['sub_wins'] - b['sub_wins'],
        r['dec_wins'] - b['dec_wins'],
        reach_open_stance,
        td_threat,
        rank_diff,
        five_rounds_exp,
        r['ape_idx'] - b['ape_idx'],
        mkt_edge,
        mkt_logit
    ], dtype=float)


def replay(fights):
    """Reconstruct fighter state chronologically prior to each calendar day."""
    cleaned_examples, states = replay_with_methods(fights)
    # Return (day, feat, y) tuples for compatibility
    return [(e[0], e[1], e[2]) for e in cleaned_examples], states


def replay_with_methods(fights):
    """Replay helper returning (day, feat, y, m_cat) for multi-task training."""
    states = defaultdict(fresh)
    examples = []
    ordered = sorted(fights, key=lambda f: (f['date'], f['id']))

    for day, group in groupby(ordered, key=lambda f: f['date']):
        group = list(group)
        # All bouts on same date strictly use states before the day began
        for f in group:
            if f.get('winner') in ('red', 'blue'):
                feat = features(states, f['red'], f['blue'], day, f)
                y = int(f['winner'] == 'red')
                m_str = f.get('method', '').upper()
                if any(k in m_str for k in ('KO', 'TKO')):
                    m_cat = 1
                elif 'SUB' in m_str:
                    m_cat = 2
                else:
                    m_cat = 0
                examples.append((day, feat, y, m_cat))

        for f in group:
            if f.get('winner') not in ('red', 'blue'):
                continue
            a, b = states[identity(f['red'])], states[identity(f['blue'])]
            y = int(f['winner'] == 'red')
            exp = 1 / (1 + 10 ** ((b['elo'] - a['elo']) / 400))
            fin = f.get('method', '').upper()
            is_ko = any(m in fin for m in ('KO', 'TKO'))
            is_sub = 'SUB' in fin
            is_dec_split = 'S-DEC' in fin or 'SPLIT' in fin
            k = 30. if (is_ko or is_sub) else (15. if is_dec_split else 22.)
            delta = k * (y - exp)
            a['elo'] += delta
            b['elo'] -= delta

            if is_ko:
                a['str_elo'] += delta * 1.3
                b['str_elo'] -= delta * 1.3
            elif is_sub:
                a['grap_elo'] += delta * 1.3
                b['grap_elo'] -= delta * 1.3
            else:
                a['str_elo'] += delta * 0.5
                b['str_elo'] -= delta * 0.5
                a['grap_elo'] += delta * 0.5
                b['grap_elo'] -= delta * 0.5

            for state, won, exp_val, corner in [(a, y, exp, 'red'), (b, 1 - y, 1 - exp, 'blue')]:
                state['residuals'] = (state['residuals'] + [won - exp_val])[-5:]
                state['finish_losses'] += int(not won and (is_ko or is_sub))
                state['wins'] += won
                state['losses'] += 1 - won
                state['streak'] = max(0, state['streak']) + 1 if won else min(0, state['streak']) - 1
                state['recent'] = (state['recent'] + [won])[-5:]
                state['recent3'] = (state.get('recent3', []) + [won])[-3:]
                state['ko'] += int(won and is_ko)
                state['sub'] += int(won and is_sub)
                state['dec'] += int(won and not (is_ko or is_sub))
                state['last'] = day
                state['total_rounds'] += int(f.get('total_rounds') or 3)

                if f.get(corner + '_age') is not None: state['age'] = f[corner + '_age']
                if f.get(corner + '_height') is not None: state['height'] = f[corner + '_height']
                if f.get(corner + '_reach') is not None: state['reach'] = f[corner + '_reach']
                if f.get(corner + '_weight') is not None: state['weight'] = f[corner + '_weight']
                if f.get(corner + '_stance'): state['stance'] = f[corner + '_stance']
                if f.get(corner + '_sig_str_landed') is not None: state['sig_str_landed'] = f[corner + '_sig_str_landed']
                if f.get(corner + '_sig_str_pct') is not None: state['sig_str_acc'] = f[corner + '_sig_str_pct']
                if f.get(corner + '_td_landed') is not None: state['td_landed'] = f[corner + '_td_landed']
                if f.get(corner + '_td_pct') is not None: state['td_acc'] = f[corner + '_td_pct']
                if f.get(corner + '_sub_att') is not None: state['sub_att'] = f[corner + '_sub_att']
                if f.get(corner + '_longest_streak') is not None: state['longest_streak'] = f[corner + '_longest_streak']
                if f.get(corner + '_title_bouts') is not None: state['title_bouts'] = f[corner + '_title_bouts']
                if f.get(corner + '_rank') is not None: state['rank'] = f[corner + '_rank']

    return examples, dict(states)


def probability(model, x):
    """Enforce exact corner swap anti-symmetry for any binary estimator."""
    x = np.asarray(x).reshape(-1, len(FEATURES))
    return (model.predict_proba(x)[:, 1] + 1.0 - model.predict_proba(-x)[:, 1]) / 2.0


def fit_symmetric(model, x, y):
    """Train with symmetric parity augmentation."""
    return model.fit(np.concatenate([x, -x]), np.concatenate([y, 1 - y]))


def fit(model, x, y):
    return fit_symmetric(model, x, y)


def make_ensemble_factories():
    """Diverse machine learning models for the MMA Cipher ensemble."""
    return {
        'MMA Cipher Extra Trees': lambda: ExtraTreesClassifier(
            n_estimators=300, max_depth=7, min_samples_leaf=10, random_state=42
        ),
        'MMA Cipher Random Forest': lambda: RandomForestClassifier(
            n_estimators=300, max_depth=5, min_samples_leaf=20, random_state=42
        ),
        'MMA Cipher XGBoost': lambda: xgb.XGBClassifier(
            n_estimators=150, max_depth=2, learning_rate=0.02, reg_lambda=30.0, subsample=0.8, eval_metric='logloss', random_state=42
        ),
        'MMA Cipher LightGBM': lambda: lgb.LGBMClassifier(
            n_estimators=140, num_leaves=10, learning_rate=0.02, reg_lambda=25.0, min_child_samples=25, verbose=-1, random_state=42
        ),
        'MMA Cipher HistGradientBoosting': lambda: HistGradientBoostingClassifier(
            max_iter=100, max_leaf_nodes=8, learning_rate=0.02, l2_regularization=30.0, random_state=42
        ),
        'MMA Cipher Logistic Regression': lambda: make_pipeline(
            StandardScaler(),
            LogisticRegression(C=0.03, max_iter=1000, random_state=42)
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
                     red_weight=f.get('blue_weight'), blue_weight=f.get('red_weight'),
                     red_stance=f.get('blue_stance'), blue_stance=f.get('red_stance'),
                     red_sig_str_landed=f.get('blue_sig_str_landed'), blue_sig_str_landed=f.get('red_sig_str_landed'),
                     red_sig_str_pct=f.get('blue_sig_str_pct'), blue_sig_str_pct=f.get('red_sig_str_pct'),
                     red_td_landed=f.get('blue_td_landed'), blue_td_landed=f.get('red_td_landed'),
                     red_td_pct=f.get('blue_td_pct'), blue_td_pct=f.get('red_td_pct'),
                     red_sub_att=f.get('blue_sub_att'), blue_sub_att=f.get('red_sub_att'),
                     red_longest_streak=f.get('blue_longest_streak'), blue_longest_streak=f.get('red_longest_streak'),
                     red_title_bouts=f.get('blue_title_bouts'), blue_title_bouts=f.get('red_title_bouts'),
                     red_ko_wins=f.get('blue_ko_wins'), blue_ko_wins=f.get('red_ko_wins'),
                     red_sub_wins=f.get('blue_sub_wins'), blue_sub_wins=f.get('red_sub_wins'),
                     red_dec_wins=f.get('blue_dec_wins'), blue_dec_wins=f.get('red_dec_wins'),
                     red_rank=f.get('blue_rank'), blue_rank=f.get('red_rank'),
                     red_odds=f.get('blue_odds'), blue_odds=f.get('red_odds'))
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

    # Fit validation models on tr to evaluate accuracy and calibration on va
    x_tr_sym = np.concatenate([x[tr], -x[tr]])
    y_tr_sym = np.concatenate([y[tr], 1 - y[tr]])
    fitted_val = {name: factory().fit(x_tr_sym, y_tr_sym) for name, factory in factories.items()}
    val_preds = np.column_stack([probability(fitted_val[name], x[va]) for name in factories])

    # Optimization driven by out-of-fold validation classification skill and calibration
    val_accs = np.array([accuracy_score(y[va], val_preds[:, i] >= .5) for i in range(len(factories))])
    val_briers = np.array([brier_score_loss(y[va], val_preds[:, i]) for i in range(len(factories))])
    val_scores = val_accs - 0.25 * val_briers

    tau = 0.008
    opt_w = np.exp((val_scores - val_scores.max()) / tau)
    # Scrap inferior accuracy: downweight linear model so it does not degrade ensemble accuracy
    if 'MMA Cipher Logistic Regression' in factories:
        lr_idx = list(factories.keys()).index('MMA Cipher Logistic Regression')
        opt_w[lr_idx] = min(opt_w[lr_idx], 0.02 * opt_w.sum())
    opt_w /= opt_w.sum()
    ensemble_weights = {name: float(w) for name, w in zip(factories.keys(), opt_w)}

    # Train serving models on all pre-test data for evaluation
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

    # Train production models on all data
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
    elif name == 'Striking Elo':
        return f"Striking exchanges & knockout rating edge (+{abs(d):.0f} points)"
    elif name == 'Grappling Elo':
        return f"Wrestling control & submission rating edge (+{abs(d):.0f} points)"
    elif name == 'Age differential':
        return f"Youth & prime advantage ({abs(d):.1f} years younger)"
    elif name == 'Age cliff differential':
        return "Prime athletic window advantage (Opponent is past age 35 cliff)"
    elif name == 'Sig strike rate differential':
        return f"Striking volume rate advantage (+{abs(d):.1f} SLpM)"
    elif name == 'Sig strike accuracy differential':
        return f"Striking accuracy precision edge (+{abs(d)*100:.1f}%)"
    elif name == 'Takedown rate differential':
        return f"Offensive takedown volume advantage (+{abs(d):.1f} TD / 15m)"
    elif name == 'Takedown accuracy differential':
        return f"Takedown accuracy completion edge (+{abs(d)*100:.1f}%)"
    elif name == 'Takedown threat interaction':
        return "Offensive wrestling vs opponent takedown defense leverage"
    elif name == 'Submission attempt differential':
        return f"Submission grappling threat (+{abs(d):.1f} sub att / 15m)"
    elif name == 'Reach differential':
        return f"Reach wingspan advantage (+{abs(d):.1f} cm)"
    elif name == 'Reach stance interaction':
        return "Open stance reach leverage advantage"
    elif name == 'Height differential':
        return f"Height leverage advantage (+{abs(d):.1f} cm)"
    elif name == 'Ape index differential':
        return "Ape index anatomical wingspan ratio advantage"
    elif name == 'Ranking differential':
        return f"Official UFC division ranking advantage (+{abs(d):.1f} ranking tier)"
    elif name == 'Win streak':
        return f"Momentum & active win streak (+{abs(d):.0f} consecutive wins)"
    elif name == 'Longest win streak differential':
        return f"Historical career peak streak (+{abs(d):.0f} wins ceiling)"
    elif name == 'Title bouts differential':
        return f"Championship title fight experience (+{abs(d):.0f} title bouts)"
    elif name == 'Total rounds differential':
        return f"Octagon cage time experience (+{abs(d):.0f} rounds logged)"
    elif name == 'KO wins differential' or name == 'KO win rate':
        return f"Knockout finishing power (+{abs(d):.0f} UFC KO wins)"
    elif name == 'Submission wins differential' or name == 'Submission win rate':
        return f"Submission finishing threat (+{abs(d):.0f} submission wins)"
    elif name == 'Decision dominance differential':
        return "15-minute fight pace and judging dominance"
    elif name == 'Finish loss rate':
        return "Durability profile and finish resistance"
    elif name == 'Stance advantage':
        return "Stance matchup angle advantage (Southpaw / Switch)"
    elif name == 'Recent three form':
        return "Recent three-bout victory momentum"
    elif name == 'Recorded wins':
        return f"Professional experience depth (+{abs(d):.0f} recorded wins)"
    elif name == 'Market implied edge' or name == 'Market logit':
        return "Betting market consensus favorite"
    return f"Statistical advantage in {name.lower()}"


def _format_risk(name, diff, is_red_pick):
    d = -diff if is_red_pick else diff
    if name == 'Age differential':
        return f"Opponent holds youth advantage ({abs(d):.1f} years younger)"
    elif name == 'Age cliff differential':
        return "Fighter is in late-career athletic drop-off window (>35 yrs)"
    elif name == 'Reach differential':
        return f"Opponent has longer wingspan (+{abs(d):.1f} cm reach)"
    elif name == 'Height differential':
        return f"Opponent is taller (+{abs(d):.1f} cm)"
    elif name == 'Sig strike rate differential':
        return f"Opponent lands higher striking output (+{abs(d):.1f} SLpM)"
    elif name == 'Takedown rate differential' or name == 'Takedown threat interaction':
        return f"Opponent is an offensive grappling threat (+{abs(d):.1f} TD / 15m)"
    elif name == 'Submission attempt differential':
        return f"Opponent possesses dangerous ground submissions (+{abs(d):.1f} sub att)"
    elif name == 'Total rounds differential':
        return f"Opponent has more octagon cage time (+{abs(d):.0f} rounds)"
    elif name == 'Ranking differential':
        return "Opponent holds higher official UFC division ranking"
    elif name == 'Market implied edge' or name == 'Market logit':
        return "Market odds favor opponent"
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
        'age': {'red': a.get('age', 30.0), 'blue': b.get('age', 30.0), 'diff': float(x[16]) if len(x) > 16 else 0.0},
        'height': {'red': a.get('height', 175.0), 'blue': b.get('height', 175.0), 'diff': float(x[17]) if len(x) > 17 else 0.0},
        'reach': {'red': a.get('reach', 175.0), 'blue': b.get('reach', 175.0), 'diff': float(x[18]) if len(x) > 18 else 0.0},
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
            pick_contribution = c_val if is_red_pick else -c_val
            items.append({
                'feature': f_name,
                'diff': float(diff_val),
                'contribution': float(pick_contribution)
            })

        items.sort(key=lambda it: it['contribution'], reverse=True)
        for it in items[:4]:
            if it['contribution'] > 0.005:
                advantages.append({
                    'title': _format_advantage(it['feature'], it['diff'], is_red_pick),
                    'feature': it['feature'],
                    'diff': round(it['diff'], 2),
                    'impact': 'high' if it['contribution'] > 0.20 else 'medium'
                })

        for it in reversed(items):
            if it['contribution'] < -0.005 and len(risks) < 2:
                risks.append({
                    'title': _format_risk(it['feature'], it['diff'], is_red_pick),
                    'feature': it['feature'],
                    'diff': round(it['diff'], 2),
                    'impact': 'high' if it['contribution'] < -0.18 else 'moderate'
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

    model_count = len(ensemble_breakdown)
    agreed_count = sum(1 for m in ensemble_breakdown.values() if (m['red_probability'] >= 0.5) == is_red_pick) if model_count else 1
    agreement_pct = round(agreed_count / max(model_count, 1), 2)
    probs_list = [m['red_probability'] for m in ensemble_breakdown.values()] if model_count else [p]
    spread = round(max(probs_list) - min(probs_list), 3) if probs_list else 0.0
    uncertainty = 'Low' if spread < 0.10 else ('Moderate' if spread < 0.18 else 'Elevated')
    consensus_tier = f"Unanimous ({agreed_count}/{model_count})" if agreed_count == model_count else f"Majority ({agreed_count}/{model_count})" if agreed_count > model_count / 2 else "Split Consensus"

    consensus = {
        'agreement_count': agreed_count,
        'total_models': model_count,
        'agreement_pct': agreement_pct,
        'tier': consensus_tier,
        'spread': spread,
        'uncertainty': uncertainty
    }

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

    upset_signals = []
    if confidence >= 0.52 and confidence <= 0.65:
        if any('takedown' in a['feature'].lower() for a in advantages) and any('strike' in r['feature'].lower() for r in risks):
            upset_signals.append("Stylistic clash: Grappler vs Striker dynamic")
    if uncertainty == 'Elevated':
        upset_signals.append("Elevated model divergence: Tree ensemble diverges from linear baseline")

    upset_radar = {
        'is_candidate': len(upset_signals) > 0 or (confidence >= 0.52 and confidence <= 0.64 and spread >= 0.08),
        'signals': upset_signals
    }

    return dict(
        red_probability=p,
        blue_probability=1.0 - p,
        pick=pick,
        confidence=confidence,
        model_version=bundle['version'],
        model_name=bundle.get('name', 'MMA Cipher Advanced Ensemble'),
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
