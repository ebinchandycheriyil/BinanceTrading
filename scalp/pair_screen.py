"""Causal, daily cointegration screening for a supplied hourly universe.

The caller supplies the universe: this does not reconstruct historical listings
or eliminate survivor bias. Repeated daily tests and correlated pairs also mean
that daily Benjamini-Hochberg correction is not a study-wide error guarantee.
"""
from collections import Counter
from itertools import combinations
import math
import warnings

import numpy as np
from statsmodels.tools.sm_exceptions import CollinearityWarning
from statsmodels.tsa.stattools import adfuller, coint


HOUR = 3_600_000
MIN_DAILY_TURNOVER = 10_000_000


def benjamini_hochberg(p_values, alpha=.05):
    """Return a mask using the full supplied hypothesis count.

    Failed/unusable tests stay in the family as p=1, rather than shrinking the
    denominator and making the surviving tests easier to pass.
    """
    if not 0 < alpha < 1:
        raise ValueError("alpha must lie between zero and one")
    values = [float(p) if p is not None and math.isfinite(p) and 0 <= p <= 1 else 1.
              for p in p_values]
    ordered = sorted(range(len(values)), key=lambda i: (values[i], i))
    cutoff = -1
    for rank, index in enumerate(ordered, 1):
        if values[index] <= alpha * rank / len(values):
            cutoff = rank
    accepted = set(ordered[:cutoff]) if cutoff >= 0 else set()
    return [i in accepted for i in range(len(values))]


def _adf_p(values):
    try:
        if np.std(values) < 1e-10:
            return None
        result = float(adfuller(values, maxlag=1, regression='c', autolag=None,
                               result_object=False)[1])
        return result if math.isfinite(result) else None
    except (ValueError, np.linalg.LinAlgError):
        return None


def _fit(log_x, log_y):
    """Fit log(y) = intercept + beta*log(x); retain raw p before filters."""
    variance = float(np.var(log_x))
    if variance < 1e-12:
        return None
    beta = float(np.cov(log_x, log_y, ddof=0)[0, 1] / variance)
    intercept = float(np.mean(log_y) - beta * np.mean(log_x))
    residual = log_y - intercept - beta * log_x
    sd = float(np.std(residual))
    if sd < 1e-12:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', CollinearityWarning)
            _, p, _ = coint(log_y, log_x, trend='c', maxlag=1, autolag=None)
    except (ValueError, np.linalg.LinAlgError, CollinearityWarning):
        return None
    if not math.isfinite(p):
        return None
    lag_variance = float(np.var(residual[:-1]))
    if lag_variance <= 1e-15:
        return None
    phi = 1 + float(np.cov(residual[:-1], np.diff(residual), ddof=0)[0, 1] / lag_variance)
    half = -math.log(2) / math.log(phi) if 0 < phi < 1 else math.inf
    return dict(beta=beta, intercept=intercept, sd=sd, p=float(p),
                half=half, phi=phi, z=float(residual[-1] / sd))


def build_pair_features(data, windows=(144, 168, 336)):
    """Return ``(features, stats)`` for every eligible daily decision time.

    ``features[ts][window]`` is a list of accepted model dictionaries. Alphabetic
    symbol order fixes x/y orientation before testing. Each dictionary contains
    x/y, beta, intercept, sd, p, half, phi, z and price-series ADF diagnostics.
    Liquidity uses estimated prior 24-hour close*base-volume turnover. Models
    exclusively use hourly candles with opening timestamp strictly below ts.

    ADF rejection of either log-price unit-root null blocks the pair; failure to
    reject is only a diagnostic, not proof of I(1) prices. BH at 5% applies to
    ALL liquidity-eligible pairs separately for each window/day, before beta,
    half-life, variance or ADF filtering. No pair-specific orientation search.
    """
    windows = tuple(sorted(set(windows)))
    if not windows or any(not isinstance(w, int) or isinstance(w, bool) or w < 48 for w in windows):
        raise ValueError("windows must contain integers of at least 48 hours")
    symbols = sorted(data)
    if len(symbols) < 2:
        raise ValueError("at least two symbols are required")
    times = [row.ts for row in data[symbols[0]]]
    if any(t % HOUR for t in times) or any(b - a != HOUR for a, b in zip(times, times[1:])):
        raise ValueError("hourly timestamps must be aligned and contiguous")
    for symbol in symbols:
        if [row.ts for row in data[symbol]] != times:
            raise ValueError("all symbols must have synchronized hourly timestamps")
        if any(not math.isfinite(row.close) or row.close <= 0 or
               not math.isfinite(row.volume) or row.volume < 0 for row in data[symbol]):
            raise ValueError("prices and volumes must be finite and valid")
    all_pairs = list(combinations(symbols, 2))
    totals = Counter()
    window_totals = {w: Counter() for w in windows}
    daily = []
    features = {}
    for i, ts in enumerate(times):
        if ts % (24 * HOUR) or i < min(windows):
            continue
        features[ts] = {w: [] for w in windows}
        turnover = {s: sum(row.close * row.volume for row in data[s][i-24:i]) for s in symbols}
        pairs = [(x, y) for x, y in all_pairs
                 if turnover[x] >= MIN_DAILY_TURNOVER and turnover[y] >= MIN_DAILY_TURNOVER]
        for window in windows:
            if i < window:
                continue
            counts = Counter(screened_windows=1, universe_pairs=len(all_pairs),
                             tested_pairs=len(pairs), illiquid_pairs=len(all_pairs)-len(pairs))
            logs = {s: np.log(np.asarray([row.close for row in data[s][i-window:i]])) for s in symbols}
            adf = {s: _adf_p(values) for s, values in logs.items()}
            models = [_fit(logs[x], logs[y]) for x, y in pairs]
            passed = benjamini_hochberg([model['p'] if model else 1. for model in models])
            for (x, y), model, significant in zip(pairs, models, passed):
                if model is None:
                    counts['fit_failed'] += 1
                    continue
                if not significant:
                    counts['fdr_rejected'] += 1
                    continue
                counts['fdr_passed'] += 1
                if adf[x] is None or adf[y] is None:
                    counts['adf_unavailable'] += 1
                    continue
                if min(adf[x], adf[y]) <= .05:
                    counts['price_adf_stationary'] += 1
                    continue
                if not .3 <= model['beta'] <= 3:
                    counts['beta_rejected'] += 1
                    continue
                if model['sd'] <= 1e-5:
                    counts['variance_rejected'] += 1
                    continue
                if not 2 <= model['half'] <= 72:
                    counts['half_life_rejected'] += 1
                    continue
                features[ts][window].append(dict(model, x=x, y=y,
                    adf_x_p=adf[x], adf_y_p=adf[y],
                    turnover_x=turnover[x], turnover_y=turnover[y],
                    formation_start=times[i-window], formation_end=times[i-1],
                    hypotheses=len(pairs), window=window))
                counts['accepted'] += 1
            daily.append(dict(ts=ts, window=window, **counts))
            totals.update(counts)
            window_totals[window].update(counts)
    stats = dict(symbols=symbols, candidate_pairs=len(all_pairs), windows=list(windows),
                 totals=dict(totals), by_window={w: dict(c) for w, c in window_totals.items()},
                 daily=daily, fdr_alpha=.05, minimum_daily_turnover=MIN_DAILY_TURNOVER,
                 universe_caveat='Supplied fixed universe; not a point-in-time listing universe.')
    return features, stats
