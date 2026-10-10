"""Same evolutionary search as ml_evolve, plus scikit-learn tree models (run inside WSL / Linux).

Adds RandomForest, ExtraTrees and HistGradientBoosting to the model families; every other rule
(splits, fitness, population, generations, runs, final test) is identical. Results go to artifacts/ml/evolve_wsl.

  wsl -d Ubuntu -u root -- bash -lc "cd /mnt/c/Users/ebinc/OneDrive/Documents/Projects/Trade/playgroundML && \
      /opt/trademl/bin/python -m daytrade.ml_evolve_sk"
"""
import multiprocessing
import os

from . import ml_evolve as E

E.OUT = os.path.join(os.path.dirname(E.OUT), 'evolve_wsl')
E.SPACE['model'] = E.SPACE['model'] + ['sk_rf', 'sk_et', 'sk_hgb']
E.MODEL_SPACE.update({
    'sk_rf': dict(trees=[200, 400], depth=[6, 10, 0], leaf=[50, 200], maxf=['sqrt', '0.5']),
    'sk_et': dict(trees=[200, 400], depth=[6, 10, 0], leaf=[50, 200], maxf=['sqrt', '0.5']),
    'sk_hgb': dict(iters=[200, 500], lr=[0.03, 0.1], leaves=[15, 31, 63], l2=[0.0, 1.0]),
})
_base_make = E.make_model


def make_model(g, seed):
    m, p = g['model'], g['mp']
    if not m.startswith('sk_'):
        return _base_make(g, seed)
    if m == 'sk_hgb':
        from sklearn.ensemble import HistGradientBoostingClassifier
        return HistGradientBoostingClassifier(max_iter=p['iters'], learning_rate=p['lr'], max_leaf_nodes=p['leaves'],
                                              l2_regularization=p['l2'], random_state=seed)
    from sklearn.ensemble import ExtraTreesClassifier, RandomForestClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import make_pipeline
    cls = RandomForestClassifier if m == 'sk_rf' else ExtraTreesClassifier
    maxf = 'sqrt' if p['maxf'] == 'sqrt' else float(p['maxf'])
    est = cls(n_estimators=p['trees'], max_depth=p['depth'] or None, min_samples_leaf=p['leaf'], max_features=maxf,
              n_jobs=3, random_state=seed)        # 6 worker processes x 3 threads fits WSL's 20 threads
    return make_pipeline(SimpleImputer(strategy='median'), est)


E.make_model = make_model

if __name__ == '__main__':
    multiprocessing.set_start_method('fork', force=True)     # workers inherit the extended model space
    E.main()
