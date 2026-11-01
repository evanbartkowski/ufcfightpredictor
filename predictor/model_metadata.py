"""Public model statements derived from the loaded serving bundle."""
from .model import FEATURES, SCHEMA

CALIBRATION_NOTE = 'Probabilities are model estimates and have not been separately calibrated. Holdout reliability bins measure agreement with outcomes; they do not apply a calibration transform.'
WEIGHT_SELECTION = 'Validation accuracy minus 0.25 times Brier score, transformed to softmax weights; the logistic component is capped before normalization. The final chronological holdout does not choose these weights.'


def describe_model(bundle):
    if not bundle:
        return None
    models = list(bundle.get('models', {})) or [bundle.get('name', 'Serving estimator')]
    estimator = bundle.get('model')
    count = getattr(estimator, 'n_features_in_', None)
    names = bundle.get('feature_names')
    if names is None and bundle.get('schema') == SCHEMA and count == len(FEATURES):
        names = list(FEATURES)
    names = names or []
    count = int(count) if count is not None else len(names)
    return dict(feature_count=count, feature_names=names, estimator_names=models,
                input_policy=bundle.get('input_policy', 'market-free'),
                market_comparison=bundle.get('metrics', {}).get('market_comparison'),
                estimator_count=len(models), calibration_applied=False,
                feature_importance_scope='Standardized logistic-component coefficients; not full-ensemble importance or causal effects.',
                calibration_method='none', calibration_note=CALIBRATION_NOTE,
                weight_selection=bundle.get('weight_selection', WEIGHT_SELECTION),
                description=f"{len(models)} fitted estimators combine {count} pre-bout inputs. "
                            + 'Serving models: ' + ', '.join(n.replace('MMA Cipher ', '') for n in models) + '.',
                limitations='Chronological replay reduces outcome leakage. Historical source completeness and the timestamps of imported attributes are not independently audited. Missing inputs use baseline values.')
