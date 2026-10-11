"""MMA Cipher Experimentation and Model Exploration Harness.
Rigorous chronological evaluation on 1,351 unseen holdout bouts.
Zero future leakage.
"""
import csv
import math
import unicodedata
from collections import defaultdict
from datetime import date
from itertools import groupby

import lightgbm as lgb
import numpy as np
from scipy.optimize import minimize
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
import xgboost as xgb


def identity(name):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(name)).casefold() if c.isalnum())


def _num(val, default=0.0):
    if val is None or val == '':
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


# Load fight-level stats from large_dataset
with open('large_dataset.csv', 'r', encoding='utf-8-sig') as f:
    ld_rows = list(csv.DictReader(f))

large_by_pair = {}
for r in ld_rows:
    large_by_pair[(identity(r['r_fighter']), identity(r['b_fighter']))] = r

# Load master rows
with open('ufc-master.csv', 'r', encoding='utf-8-sig') as f:
    master_rows = list(csv.DictReader(f))

print(f"Loaded {len(master_rows)} master bouts and {len(ld_rows)} detailed bouts.")


def fresh_state():
    return dict(
        elo=1500.0,
        striking_elo=1500.0,
        grappling_elo=1500.0,
        rd=350.0,  # Glicko rating deviation (uncertainty)
        opp_elos=[],  # strength of schedule
        wins=0, losses=0, streak=0,
        recent=[], recent3=[],
        ko_wins=0, sub_wins=0, dec_wins=0,
        ko_losses=0, sub_losses=0,
        residuals=[],
        last=None,
        total_rounds=0,
        total_time_min=0.0,
        # Striking
        sig_str_landed=0.0,
        sig_str_absorbed=0.0,
        sig_str_att=0.0,
        opp_sig_str_att=0.0,
        kd_scored=0.0,
        kd_absorbed=0.0,
        # Grappling
        td_landed=0.0,
        td_att=0.0,
        opp_td_landed=0.0,
        opp_td_att=0.0,
        sub_att=0.0,
        ctrl_sec=0.0,
        # Physical
        age=30.0, height=175.0, reach=175.0, weight=155.0, stance='Orthodox'
    )


def compute_fighter_features(state, day, attrs, opp_state=None, is_title=False, is_5_rounds=False):
    n = state['wins'] + state['losses']
    rest_days = min((date.fromisoformat(day) - date.fromisoformat(state['last'])).days, 1460) if state['last'] else 365
    
    # Inactivity decay on RD
    rd = min(math.sqrt(state['rd']**2 + 0.1 * rest_days), 350.0)
    
    # Bayesian shrinkage / smoothing with priors:
    # Prior: 3.5 SLpM, 3.5 SApM, 45% sig str acc, 55% sig str def, 1.5 TD/15m, 40% TD acc, 60% TD def
    mins = max(state['total_time_min'], 0.1)
    prior_mins = 15.0  # 1 fight worth of prior
    
    slpm = (state['sig_str_landed'] + 3.5 * prior_mins) / (mins + prior_mins)
    sapm = (state['sig_str_absorbed'] + 3.5 * prior_mins) / (mins + prior_mins)
    
    sig_acc = (state['sig_str_landed'] + 15.0) / max(state['sig_str_att'] + 33.3, 1.0)
    sig_def = 1.0 - (state['sig_str_absorbed'] + 15.0) / max(state['opp_sig_str_att'] + 33.3, 1.0)
    sig_def = max(0.1, min(0.9, sig_def))
    
    td_rate = ((state['td_landed'] + 1.5 * (prior_mins / 15.0)) * 15.0) / (mins + prior_mins)
    td_acc = (state['td_landed'] + 2.0) / max(state['td_att'] + 5.0, 1.0)
    td_def = 1.0 - (state['opp_td_landed'] + 2.0) / max(state['opp_td_att'] + 5.0, 1.0)
    td_def = max(0.1, min(0.95, td_def))
    
    kd_rate = (state['kd_scored'] * 15.0) / mins if mins > 5 else 0.2
    kd_absorbed_rate = (state['kd_absorbed'] * 15.0) / mins if mins > 5 else 0.2
    
    ctrl_pct = state['ctrl_sec'] / max(mins * 60.0, 1.0)
    sub_rate = (state['sub_att'] * 15.0) / mins if mins > 5 else 0.5
    
    sos = np.mean(state['opp_elos'][-5:]) if state['opp_elos'] else 1500.0
    
    # Age and physical
    age = _num(attrs.get('age'), state.get('age', 30.0))
    height = _num(attrs.get('height'), state.get('height', 175.0))
    reach = _num(attrs.get('reach'), state.get('reach', 175.0))
    weight = _num(attrs.get('weight'), state.get('weight', 155.0))
    stance = attrs.get('stance') or state.get('stance', 'Orthodox')
    st_val = 1.0 if 'southpaw' in str(stance).lower() else (0.5 if 'switch' in str(stance).lower() else 0.0)
    
    # Non-linear age penalty (>35 in light divisions)
    is_light = 1.0 if weight <= 170.0 else 0.0
    age_cliff = max(0.0, age - 35.0) * (1.5 if is_light else 0.8)
    
    # Ring rust / inactivity penalty
    rust = max(0.0, (rest_days - 365) / 365.0)
    
    # Form
    win_rate = (state['wins'] + 2) / (n + 4)
    rec5 = (sum(state['recent'][-5:]) + 1) / (len(state['recent'][-5:]) + 2)
    rec3 = (sum(state['recent'][-3:]) + 1) / (len(state['recent'][-3:]) + 2)
    streak = state['streak']
    
    ko_rate = state['ko_wins'] / max(n, 1)
    sub_win_rate = state['sub_wins'] / max(n, 1)
    finish_loss_rate = (state['ko_losses'] + state['sub_losses']) / (n + 4)
    chin_durability = 1.0 - (state['ko_losses'] * 2.0 + state['kd_absorbed']) / (n + 4)
    
    return {
        'elo': state['elo'],
        'striking_elo': state['striking_elo'],
        'grappling_elo': state['grappling_elo'],
        'rd': rd,
        'sos': sos,
        'n': n,
        'win_rate': win_rate,
        'rec5': rec5,
        'rec3': rec3,
        'streak': streak,
        'ko_rate': ko_rate,
        'sub_win_rate': sub_win_rate,
        'finish_loss_rate': finish_loss_rate,
        'chin_durability': chin_durability,
        'rest_days': rest_days,
        'rust': rust,
        'age': age,
        'age_cliff': age_cliff,
        'height': height,
        'reach': reach,
        'weight': weight,
        'st_val': st_val,
        'slpm': slpm,
        'sapm': sapm,
        'sig_acc': sig_acc,
        'sig_def': sig_def,
        'td_rate': td_rate,
        'td_acc': td_acc,
        'td_def': td_def,
        'kd_rate': kd_rate,
        'kd_absorbed_rate': kd_absorbed_rate,
        'ctrl_pct': ctrl_pct,
        'sub_rate': sub_rate,
        'total_rounds': state['total_rounds']
    }


def build_pair_features(f_red, f_blue, market_implied_red=None):
    # Differentials
    elo_diff = f_red['elo'] - f_blue['elo']
    str_elo_diff = f_red['striking_elo'] - f_blue['striking_elo']
    grap_elo_diff = f_red['grappling_elo'] - f_blue['grappling_elo']
    rd_diff = f_red['rd'] - f_blue['rd']
    sos_diff = f_red['sos'] - f_blue['sos']
    exp_diff = f_red['n'] - f_blue['n']
    win_rate_diff = f_red['win_rate'] - f_blue['win_rate']
    rec5_diff = f_red['rec5'] - f_blue['rec5']
    rec3_diff = f_red['rec3'] - f_blue['rec3']
    streak_diff = f_red['streak'] - f_blue['streak']
    
    age_diff = f_red['age'] - f_blue['age']
    age_cliff_diff = f_red['age_cliff'] - f_blue['age_cliff']
    height_diff = f_red['height'] - f_blue['height']
    reach_diff = f_red['reach'] - f_blue['reach']
    stance_diff = f_red['st_val'] - f_blue['st_val']
    
    slpm_diff = f_red['slpm'] - f_blue['slpm']
    sapm_diff = f_red['sapm'] - f_blue['sapm']
    net_str_diff = (f_red['slpm'] - f_red['sapm']) - (f_blue['slpm'] - f_blue['sapm'])
    sig_acc_diff = f_red['sig_acc'] - f_blue['sig_acc']
    sig_def_diff = f_red['sig_def'] - f_blue['sig_def']
    
    td_rate_diff = f_red['td_rate'] - f_blue['td_rate']
    td_acc_diff = f_red['td_acc'] - f_blue['td_acc']
    td_def_diff = f_red['td_def'] - f_blue['td_def']
    
    kd_rate_diff = f_red['kd_rate'] - f_blue['kd_rate']
    kd_abs_diff = f_red['kd_absorbed_rate'] - f_blue['kd_absorbed_rate']
    chin_diff = f_red['chin_durability'] - f_blue['chin_durability']
    ctrl_diff = f_red['ctrl_pct'] - f_blue['ctrl_pct']
    sub_diff = f_red['sub_rate'] - f_blue['sub_rate']
    rust_diff = f_red['rust'] - f_blue['rust']
    rounds_diff = f_red['total_rounds'] - f_blue['total_rounds']
    
    # High-impact stylistic clashes / matchup interaction terms:
    # 1. Takedown clash: Can Red take Blue down vs Can Blue take Red down?
    td_clash = (f_red['td_rate'] * (1.0 - f_blue['td_def'])) - (f_blue['td_rate'] * (1.0 - f_red['td_def']))
    
    # 2. Striking volume vs defense clash
    str_clash = (f_red['slpm'] * (1.0 - f_blue['sig_def'])) - (f_blue['slpm'] * (1.0 - f_red['sig_def']))
    
    # 3. Knockout threat vs chin vulnerability clash
    ko_chin_clash = (f_red['kd_rate'] * (1.0 - f_blue['chin_durability'])) - (f_blue['kd_rate'] * (1.0 - f_red['chin_durability']))
    
    # 4. Reach advantage interaction with stance
    reach_stance_inter = reach_diff * (1.0 if (f_red['st_val'] > 0 or f_blue['st_val'] > 0) else 0.5)

    stats_vec = [
        elo_diff, str_elo_diff, grap_elo_diff, rd_diff, sos_diff, exp_diff,
        win_rate_diff, rec5_diff, rec3_diff, streak_diff,
        age_diff, age_cliff_diff, height_diff, reach_diff, stance_diff,
        slpm_diff, sapm_diff, net_str_diff, sig_acc_diff, sig_def_diff,
        td_rate_diff, td_acc_diff, td_def_diff,
        kd_rate_diff, kd_abs_diff, chin_diff, ctrl_diff, sub_diff,
        rust_diff, rounds_diff,
        td_clash, str_clash, ko_chin_clash, reach_stance_inter
    ]
    
    if market_implied_red is not None:
        # Implied prob centered at 0 (from -0.5 to +0.5)
        mkt_centered = market_implied_red - 0.5
        market_vec = stats_vec + [mkt_centered, np.sign(mkt_centered) * (mkt_centered**2)]
        return np.array(stats_vec, dtype=float), np.array(market_vec, dtype=float)
    
    return np.array(stats_vec, dtype=float), None


# Process chronological simulation
ordered = sorted(master_rows, key=lambda r: (r['Date'], identity(r['RedFighter']), identity(r['BlueFighter'])))
states = defaultdict(fresh_state)
examples_stats = []
examples_market = []

for day, group in groupby(ordered, key=lambda r: r['Date']):
    group = list(group)
    
    # Predict all bouts on this date BEFORE applying any updates
    for r in group:
        if r['Winner'].lower() not in ('red', 'blue'):
            continue
        
        red_id = identity(r['RedFighter'])
        blue_id = identity(r['BlueFighter'])
        
        red_state = states[red_id]
        blue_state = states[blue_id]
        
        # Pre-bout attributes from master row
        red_attrs = {
            'age': r.get('RedAge'), 'height': r.get('RedHeightCms'),
            'reach': r.get('RedReachCms'), 'weight': r.get('RedWeightLbs'),
            'stance': r.get('RedStance')
        }
        blue_attrs = {
            'age': r.get('BlueAge'), 'height': r.get('BlueHeightCms'),
            'reach': r.get('BlueReachCms'), 'weight': r.get('BlueWeightLbs'),
            'stance': r.get('BlueStance')
        }
        
        f_red = compute_fighter_features(red_state, day, red_attrs)
        f_blue = compute_fighter_features(blue_state, day, blue_attrs)
        
        # Market implied probability if available
        red_odds = _num(r.get('RedOdds'), None)
        blue_odds = _num(r.get('BlueOdds'), None)
        market_p = None
        if red_odds is not None and blue_odds is not None:
            p_r = (100.0 / (red_odds + 100.0)) if red_odds > 0 else (-red_odds / (-red_odds + 100.0))
            p_b = (100.0 / (blue_odds + 100.0)) if blue_odds > 0 else (-blue_odds / (-blue_odds + 100.0))
            market_p = p_r / (p_r + p_b)
        else:
            market_p = 0.5  # neutral prior when unquoted
            
        vec_stats, vec_market = build_pair_features(f_red, f_blue, market_p)
        y = 1 if r['Winner'].lower() == 'red' else 0
        
        examples_stats.append((day, vec_stats, y))
        examples_market.append((day, vec_market, y, market_p))

    # Update states with fight results
    for r in group:
        if r['Winner'].lower() not in ('red', 'blue'):
            continue
        
        red_id = identity(r['RedFighter'])
        blue_id = identity(r['BlueFighter'])
        a, b = states[red_id], states[blue_id]
        y = 1 if r['Winner'].lower() == 'red' else 0
        
        # Fight details from large_dataset if available
        ld = large_by_pair.get((red_id, blue_id)) or large_by_pair.get((blue_id, red_id))
        is_red_first = ld and identity(ld.get('r_fighter', '')) == red_id
        
        # Fight duration in minutes
        fight_time_sec = _num(r.get('TotalFightTimeSecs'), 900.0)
        mins = max(fight_time_sec / 60.0, 1.0)
        rounds = int(_num(r.get('FinishRound'), _num(r.get('NumberOfRounds'), 3)))
        
        finish_type = r.get('Finish', '').upper()
        is_ko = any(k in finish_type for k in ('KO', 'TKO'))
        is_sub = 'SUB' in finish_type
        is_finish = is_ko or is_sub
        
        # Margin of victory adjustment on Elo
        # K-factor: 32 for early finish, 24 for late finish, 18 for decision
        if is_finish and rounds <= 1:
            k = 32.0
        elif is_finish:
            k = 26.0
        else:
            k = 18.0
            
        exp_a = 1.0 / (1.0 + 10.0 ** ((b['elo'] - a['elo']) / 400.0))
        delta = k * (y - exp_a)
        a['elo'] += delta
        b['elo'] -= delta
        
        # Update Striking Elo & Grappling Elo
        if is_ko:
            a['striking_elo'] += (k * 1.2) * (y - exp_a)
            b['striking_elo'] -= (k * 1.2) * (y - exp_a)
        elif is_sub:
            a['grappling_elo'] += (k * 1.2) * (y - exp_a)
            b['grappling_elo'] -= (k * 1.2) * (y - exp_a)
        else:
            a['striking_elo'] += (k * 0.5) * (y - exp_a)
            b['striking_elo'] -= (k * 0.5) * (y - exp_a)
            a['grappling_elo'] += (k * 0.5) * (y - exp_a)
            b['grappling_elo'] -= (k * 0.5) * (y - exp_a)
            
        # Glicko RD decrease on fight
        a['rd'] = max(60.0, math.sqrt(max(a['rd']**2 - 1200.0, 3600.0)))
        b['rd'] = max(60.0, math.sqrt(max(b['rd']**2 - 1200.0, 3600.0)))
        
        # Strength of schedule tracking
        a['opp_elos'].append(b['elo'])
        b['opp_elos'].append(a['elo'])
        
        # Basic records
        a['wins'] += y
        a['losses'] += (1 - y)
        b['wins'] += (1 - y)
        b['losses'] += y
        
        a['streak'] = (a['streak'] + 1) if y == 1 else -1
        b['streak'] = (b['streak'] + 1) if y == 0 else -1
        
        a['recent'].append(y)
        b['recent'].append(1 - y)
        a['recent3'].append(y)
        b['recent3'].append(1 - y)
        
        a['ko_wins'] += int(y == 1 and is_ko)
        b['ko_wins'] += int(y == 0 and is_ko)
        a['sub_wins'] += int(y == 1 and is_sub)
        b['sub_wins'] += int(y == 0 and is_sub)
        a['ko_losses'] += int(y == 0 and is_ko)
        b['ko_losses'] += int(y == 1 and is_ko)
        a['sub_losses'] += int(y == 0 and is_sub)
        b['sub_losses'] += int(y == 1 and is_sub)
        
        a['last'] = day
        b['last'] = day
        a['total_rounds'] += rounds
        b['total_rounds'] += rounds
        a['total_time_min'] += mins
        b['total_time_min'] += mins
        
        # Per-fight strike & grappling tracking from large_dataset
        if ld:
            r_str = _num(ld.get('r_sig_str' if is_red_first else 'b_sig_str'))
            b_str = _num(ld.get('b_sig_str' if is_red_first else 'r_sig_str'))
            r_att = _num(ld.get('r_sig_str_att' if is_red_first else 'b_sig_str_att'))
            b_att = _num(ld.get('b_sig_str_att' if is_red_first else 'r_sig_str_att'))
            
            r_td = _num(ld.get('r_td' if is_red_first else 'b_td'))
            b_td = _num(ld.get('b_td' if is_red_first else 'r_td'))
            r_td_att = _num(ld.get('r_td_att' if is_red_first else 'b_td_att'))
            b_td_att = _num(ld.get('b_td_att' if is_red_first else 'r_td_att'))
            
            r_kd = _num(ld.get('r_kd' if is_red_first else 'b_kd'))
            b_kd = _num(ld.get('b_kd' if is_red_first else 'r_kd'))
            r_ctrl = _num(ld.get('r_ctrl_sec' if is_red_first else 'b_ctrl_sec'))
            b_ctrl = _num(ld.get('b_ctrl_sec' if is_red_first else 'r_ctrl_sec'))
            
            a['sig_str_landed'] += r_str
            a['sig_str_absorbed'] += b_str
            a['sig_str_att'] += r_att
            a['opp_sig_str_att'] += b_att
            a['td_landed'] += r_td
            a['td_att'] += r_td_att
            a['opp_td_landed'] += b_td
            a['opp_td_att'] += b_td_att
            a['kd_scored'] += r_kd
            a['kd_absorbed'] += b_kd
            a['ctrl_sec'] += r_ctrl
            
            b['sig_str_landed'] += b_str
            b['sig_str_absorbed'] += r_str
            b['sig_str_att'] += b_att
            b['opp_sig_str_att'] += r_att
            b['td_landed'] += b_td
            b['td_att'] += b_td_att
            b['opp_td_landed'] += r_td
            b['opp_td_att'] += r_td_att
            b['kd_scored'] += b_kd
            b['kd_absorbed'] += r_kd
            b['ctrl_sec'] += b_ctrl
        else:
            # Fallback approximations from pre-fight rates if fight not in large_dataset
            a['sig_str_landed'] += _num(r.get('RedAvgSigStrLanded'), 3.5) * mins
            a['sig_str_absorbed'] += 3.0 * mins
            b['sig_str_landed'] += _num(r.get('BlueAvgSigStrLanded'), 3.5) * mins
            b['sig_str_absorbed'] += 3.0 * mins

print(f"Processed {len(examples_stats)} historical examples.")

# Chronological Holdout Split: Exactly 80% split on dates
days_all = sorted({e[0] for e in examples_stats})
test_start = days_all[int(len(days_all) * 0.8)]
val_start = days_all[int(len(days_all) * 0.65)]

dates = np.array([e[0] for e in examples_stats])
te_mask = dates >= test_start
va_mask = (dates >= val_start) & (dates < test_start)
tr_mask = dates < val_start

x_stats = np.array([e[1] for e in examples_stats])
x_mkt = np.array([e[1] for e in examples_market])
y_all = np.array([e[2] for e in examples_stats])
mkt_probs = np.array([e[3] for e in examples_market])

print(f"Train fights: {tr_mask.sum()}, Val fights: {va_mask.sum()}, Holdout test fights: {te_mask.sum()}")
print(f"Holdout date range: {test_start} to {days_all[-1]}")

def eval_predictions(name, y_true, p_pred):
    acc = accuracy_score(y_true, p_pred >= 0.5)
    ll = log_loss(y_true, np.clip(p_pred, 1e-6, 1.0 - 1e-6), labels=[0, 1])
    brier = brier_score_loss(y_true, p_pred)
    corr = int(((p_pred >= 0.5) == y_true).sum())
    tot = len(y_true)
    print(f"{name:32s} Acc: {acc*100:.2f}% ({corr}/{tot}) | LogLoss: {ll:.4f} | Brier: {brier:.4f}")
    return acc, ll, brier

# Symmetrized training & prediction
def fit_sym(model, x, y):
    x_sym = np.concatenate([x, -x])
    y_sym = np.concatenate([y, 1 - y])
    return model.fit(x_sym, y_sym)

def sym_predict_proba(model, x):
    return (model.predict_proba(x)[:, 1] + 1.0 - model.predict_proba(-x)[:, 1]) / 2.0

print("\n--- BENCHMARK: STATISTICS-ONLY ENSEMBLE ---")
x_tr, y_tr = x_stats[~te_mask], y_all[~te_mask]
x_te, y_te = x_stats[te_mask], y_all[te_mask]

models_stats = {
    'Logistic Regression': make_pipeline(StandardScaler(), LogisticRegression(C=0.015, max_iter=1000, random_state=42)),
    'HistGradientBoosting': HistGradientBoostingClassifier(max_iter=120, max_leaf_nodes=12, learning_rate=0.03, l2_regularization=15.0, random_state=42),
    'LightGBM': lgb.LGBMClassifier(n_estimators=110, num_leaves=12, learning_rate=0.03, reg_lambda=15.0, verbose=-1, random_state=42),
    'XGBoost': xgb.XGBClassifier(n_estimators=100, max_depth=3, learning_rate=0.03, reg_lambda=15.0, eval_metric='logloss', random_state=42),
    'Random Forest': RandomForestClassifier(n_estimators=180, max_depth=6, min_samples_leaf=15, random_state=42),
    'Extra Trees': ExtraTreesClassifier(n_estimators=180, max_depth=6, min_samples_leaf=15, random_state=42),
    'MLP Neural Net': make_pipeline(StandardScaler(), MLPClassifier(hidden_layer_sizes=(48, 24), alpha=0.1, max_iter=350, random_state=42))
}

stats_preds_te = {}
for name, m in models_stats.items():
    fit_sym(m, x_tr, y_tr)
    p = sym_predict_proba(m, x_te)
    stats_preds_te[name] = p
    eval_predictions(name, y_te, p)

# Weighted combination
p_stats_mean = np.mean(list(stats_preds_te.values()), axis=0)
eval_predictions("Stats Ensemble (Equal Mean)", y_te, p_stats_mean)

# Elo alone baseline
elo_diff_te = x_te[:, 0]
p_elo_te = 1.0 / (1.0 + 10.0 ** (-elo_diff_te / 400.0))
eval_predictions("Glicko/Elo Alone", y_te, p_elo_te)


print("\n--- BENCHMARK: MARKET-INFORMED ENSEMBLE ---")
x_mkt_tr = x_mkt[~te_mask]
x_mkt_te = x_mkt[te_mask]

# Pure market baseline on holdout
eval_predictions("Pure Market Odds Baseline", y_te, mkt_probs[te_mask])

models_mkt = {
    'Mkt Logistic Regression': make_pipeline(StandardScaler(), LogisticRegression(C=0.02, max_iter=1000, random_state=42)),
    'Mkt HistGradientBoosting': HistGradientBoostingClassifier(max_iter=120, max_leaf_nodes=12, learning_rate=0.03, l2_regularization=15.0, random_state=42),
    'Mkt LightGBM': lgb.LGBMClassifier(n_estimators=110, num_leaves=12, learning_rate=0.03, reg_lambda=15.0, verbose=-1, random_state=42),
    'Mkt XGBoost': xgb.XGBClassifier(n_estimators=100, max_depth=3, learning_rate=0.03, reg_lambda=15.0, eval_metric='logloss', random_state=42),
    'Mkt Random Forest': RandomForestClassifier(n_estimators=180, max_depth=6, min_samples_leaf=15, random_state=42),
}

mkt_preds_te = {}
for name, m in models_mkt.items():
    fit_sym(m, x_mkt_tr, y_tr)
    p = sym_predict_proba(m, x_mkt_te)
    mkt_preds_te[name] = p
    eval_predictions(name, y_te, p)

p_mkt_mean = np.mean(list(mkt_preds_te.values()), axis=0)
eval_predictions("Market Ensemble (Equal Mean)", y_te, p_mkt_mean)

# Optimal Nelder-Mead on Validation Fold
print("\n--- OPTIMAL STACKING & NELDER-MEAD VALIDATION ENSEMBLE ---")
x_mkt_val_tr = x_mkt[tr_mask]
y_val_tr = y_all[tr_mask]
x_mkt_val = x_mkt[va_mask]
y_val = y_all[va_mask]

fitted_val_models = {}
for name, m in models_mkt.items():
    fitted_val_models[name] = fit_sym(m, x_mkt_val_tr, y_val_tr)

val_preds = np.column_stack([sym_predict_proba(fitted_val_models[name], x_mkt_val) for name in models_mkt])

def opt_objective(w):
    w = np.maximum(w, 0)
    if w.sum() <= 1e-9: return 10.0
    w = w / w.sum()
    p_blend = np.clip(val_preds @ w, 1e-6, 1.0 - 1e-6)
    return log_loss(y_val, p_blend, labels=[0, 1])

res = minimize(opt_objective, np.ones(len(models_mkt))/len(models_mkt), method='Nelder-Mead')
opt_w = np.maximum(res.x, 0)
opt_w /= opt_w.sum()
print("Optimized Model Weights:", {name: round(float(w), 3) for name, w in zip(models_mkt.keys(), opt_w)})

te_matrix = np.column_stack([mkt_preds_te[name] for name in models_mkt])
p_opt = te_matrix @ opt_w
eval_predictions("Optimized Market Ensemble", y_te, p_opt)
