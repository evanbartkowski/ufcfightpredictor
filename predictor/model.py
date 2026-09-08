"""Features reconstructed from prior outcomes, never today's career totals."""
import hashlib
import json
from collections import defaultdict
from datetime import date
from itertools import groupby

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss
from sklearn.pipeline import make_pipeline
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import StandardScaler

from .store import identity, now

FEATURES = ['Elo', 'Recorded wins', 'Recorded losses', 'Win streak', 'Recent form',
            'KO win rate', 'Submission win rate', 'Experience', 'Days since last fight',
            'Recent overperformance',
            'Smoothed win rate', 'Finish loss rate', 'Recent three form', 'Log inactivity']
SCHEMA = 3


def fresh():
    return dict(elo=1500., wins=0, losses=0, streak=0, recent=[], ko=0, sub=0, last=None, residuals=[], finish_losses=0)


def vector(state, day):
    n = state['wins'] + state['losses']
    rest = min((date.fromisoformat(day) - date.fromisoformat(state['last'])).days, 1095) if state['last'] else 365
    return np.array([state['elo'], state['wins'], state['losses'], state['streak'],
                     (sum(state['recent']) + 1) / (len(state['recent']) + 2),
                     state['ko'] / max(n, 1), state['sub'] / max(n, 1), n, rest,
                     np.mean(state['residuals']) if state['residuals'] else 0.,
                     (state['wins']+2)/(n+4), state['finish_losses']/(n+4),
                     (sum(state['recent'][-3:])+1)/(len(state['recent'][-3:])+2),
                     np.log1p(rest)], dtype=float)


def features(states, red, blue, day):
    return vector(states.get(identity(red), fresh()), day) - vector(states.get(identity(blue), fresh()), day)


def replay(fights):
    states = defaultdict(fresh)
    examples = []
    ordered = sorted(fights, key=lambda f: (f['date'], f['id']))
    for day, group in groupby(ordered, key=lambda f: f['date']):
        group = list(group)
        # All bouts on the same day use states from before the day began.
        for f in group:
            if f['winner'] in ('red', 'blue'):
                examples.append((day, features(states, f['red'], f['blue'], day), int(f['winner'] == 'red')))
        for f in group:
            if f['winner'] not in ('red', 'blue'):
                continue
            a, b = states[identity(f['red'])], states[identity(f['blue'])]
            y = int(f['winner'] == 'red')
            expected = 1 / (1 + 10 ** ((b['elo'] - a['elo']) / 400))
            delta = 24 * (y - expected)
            a['elo'] += delta
            b['elo'] -= delta
            for state, won, expectation in [(a,y,expected),(b,1-y,1-expected)]:
                state['residuals']=(state['residuals']+[won-expectation])[-5:]
                state['finish_losses']+=int(not won and any(m in f.get('method','').upper() for m in ('KO','SUB')))
                state['wins'] += won
                state['losses'] += 1-won
                state['streak'] = max(0, state['streak']) + 1 if won else min(0, state['streak']) - 1
                state['recent'] = (state['recent'] + [won])[-5:]
                state['ko'] += int(won and 'KO' in f.get('method', '').upper())
                state['sub'] += int(won and 'SUB' in f.get('method', '').upper())
                state['last'] = day
    return examples, dict(states)


def probability(model, x):
    x = np.asarray(x).reshape(-1, len(FEATURES))
    # Swap symmetry: changing the corner cannot change the predicted winner.
    return (model.predict_proba(x)[:, 1] + 1 - model.predict_proba(-x)[:, 1]) / 2


def fit(model, x, y):
    return model.fit(np.concatenate([x, -x]), np.concatenate([y, 1-y]))


def train(fights):
    # UFCStats lists winners first for completed bouts. Evaluate in a deterministic
    # alphabetical order so an always-first baseline cannot exploit that layout.
    canonical = []
    for f in fights:
        if identity(f['red']) > identity(f['blue']):
            f = dict(f, red=f['blue'], blue=f['red'],
                     winner={'red':'blue', 'blue':'red'}.get(f['winner'], f['winner']))
        canonical.append(f)
    examples, states = replay(canonical)
    days = sorted({e[0] for e in examples})
    if len(days) < 10:
        raise ValueError('At least 10 event dates are required for chronological evaluation.')
    val_start, test_start = days[int(len(days)*.65)], days[int(len(days)*.8)]
    x = np.array([e[1] for e in examples]); y = np.array([e[2] for e in examples])
    dates = np.array([e[0] for e in examples])
    tr, va, te = dates < val_start, (dates >= val_start) & (dates < test_start), dates >= test_start
    def logistic(indices, c):
        return make_pipeline(ColumnTransformer([('features','passthrough',indices)]),
                             StandardScaler(), LogisticRegression(C=c,max_iter=1000))
    factories = {
        'Logistic regression (baseline)': lambda: logistic(list(range(9)),.1),
        'Logistic regression (baseline, stronger regularization)': lambda: logistic(list(range(9)),.001),
        'Logistic regression (recent performance)': lambda: logistic(list(range(len(FEATURES))),.1),
        'Logistic regression (recent performance, stronger regularization)': lambda: logistic(list(range(len(FEATURES))),.001),
        'Gradient boosting (baseline)': lambda: make_pipeline(
            ColumnTransformer([('features','passthrough',list(range(9)))]),
            HistGradientBoostingClassifier(max_iter=130,max_leaf_nodes=7,learning_rate=.045,
                l2_regularization=8,early_stopping=False,random_state=42)),
    }
    # Candidate selection uses expanding historical windows, never the final 20%.
    folds=[(.45,.55),(.55,.65),(.65,.8)]
    scores, fold_scores = {}, {}
    for name, factory in factories.items():
        losses=[]
        for start,end in folds:
            train_mask=dates<days[int(len(days)*start)]
            validation_mask=(dates>=days[int(len(days)*start)]) & (dates<days[int(len(days)*end)])
            candidate=fit(factory(),x[train_mask],y[train_mask])
            losses.append(float(log_loss(y[validation_mask],probability(candidate,x[validation_mask]),labels=[0,1])))
        fold_scores[name]=losses
        scores[name]=float(np.mean(losses))
    selected = min(scores, key=scores.get)
    evaluated = fit(factories[selected](), x[~te], y[~te])
    p = probability(evaluated, x[te])
    elo = 1 / (1 + 10 ** (-x[te, 0] / 400))
    metrics = dict(accuracy=float(accuracy_score(y[te], p >= .5)),
                   brier=float(brier_score_loss(y[te], p)), log_loss=float(log_loss(y[te], p, labels=[0, 1])),
                   elo_accuracy=float(accuracy_score(y[te], elo >= .5)),
                   majority_accuracy=float(accuracy_score(y[te], np.full(te.sum(), int(np.mean(y[~te]) >= .5)))),
                   test_fights=int(te.sum()), test_start=test_start, test_end=days[-1],
                   training_fights=len(examples), validation_log_loss=scores, validation_fold_log_loss=fold_scores,
                   validation_scheme='Three expanding windows; final 20% held out')
    version = hashlib.sha256((str(SCHEMA)+json.dumps(sorted(fights, key=lambda f:f['id']), sort_keys=True)).encode()).hexdigest()[:12]
    return dict(model=fit(factories[selected](), x, y), states=states, metrics=metrics,
                name=selected, version=version, schema=SCHEMA, trained_at=now(), through=days[-1])


def predict(bundle, red, blue, day):
    x = features(bundle['states'], red, blue, day)
    p = float(probability(bundle['model'], x)[0])
    a, b = bundle['states'].get(identity(red), fresh()), bundle['states'].get(identity(blue), fresh())
    return dict(red_probability=p, blue_probability=1-p, pick=red if p >= .5 else blue,
                confidence=max(p, 1-p), model_version=bundle['version'], features=x.tolist(),
                feature_names=FEATURES, low_history=min(a['wins']+a['losses'], b['wins']+b['losses']) < 3,
                red_history=a, blue_history=b)
