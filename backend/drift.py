"""Vyhodnotenie driftu písania pri mesačnej obnove šablóny.

Porovnáva nové vzorky so šablónou z predchádzajúceho cyklu a rozhoduje, či sa písanie
zlepšilo, zhoršilo alebo ostalo stabilné. Podľa toho sa upraví prah:

* zlepšenie  -> prah sa môže sprísniť (presnejšia šablóna), najviac o THRESHOLD_MAX_SHRINK
* stabilné   -> šablóna sa len doplní novými vzorkami
* zhoršenie  -> prah sa môže uvoľniť, najviac o THRESHOLD_MAX_GROWTH za cyklus; ak by to
                nestačilo, zostane na stropu a používateľ sa upozorní, že treba novú registráciu
"""
from __future__ import annotations

import math
import warnings

import numpy as np

from .features import FEATURE_NAMES
from .scoring import score_with_stats, template_to_stats

IDX_DD = FEATURE_NAMES.index("median_dd")

IMPROVE_SPEED_PCT = -5.0     # o 5 % rýchlejšie
IMPROVE_CONSISTENCY_PCT = -10.0
DEGRADE_SPEED_PCT = 10.0     # o 10 % pomalšie
DEGRADE_CONSISTENCY_PCT = 15.0
MIN_SPREAD_DIMS = 5

VERDICT_LABELS = {"improved": "zlepšenie", "stable": "stabilné", "degraded": "zhoršenie"}


def _c4(n: np.ndarray) -> np.ndarray:
    """Korekcia skreslenia smerodajnej odchýlky pri malom n (pri n=3 podhodnocuje o ~11 %)."""
    return np.array([
        math.sqrt(2.0 / (k - 1)) * math.exp(math.lgamma(k / 2) - math.lgamma((k - 1) / 2)) if k >= 2 else np.nan
        for k in n
    ])


def _spread(matrix: np.ndarray, min_obs: int = 3) -> np.ndarray:
    """Rozptyl každého príznaku, opravený na rozdielny počet pozorovaní (nové sady sú malé)."""
    counts = np.sum(~np.isnan(matrix), axis=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        spread = np.nanstd(matrix, axis=0, ddof=1) / _c4(counts)
    spread[counts < min_obs] = np.nan
    return spread


def _col_median(matrix: np.ndarray, idx: int) -> float:
    column = matrix[:, idx]
    column = column[np.isfinite(column)]
    return float(np.median(column)) if column.size else float("nan")


def analyze_drift(old_matrix, new_matrix, old_template: dict) -> dict:
    old = np.asarray(old_matrix, dtype=float)
    new = np.asarray(new_matrix, dtype=float)

    dd_old, dd_new = _col_median(old, IDX_DD), _col_median(new, IDX_DD)
    speed = 100.0 * (dd_new - dd_old) / dd_old if np.isfinite(dd_old) and dd_old > 0 and np.isfinite(dd_new) else None

    so, sn = _spread(old), _spread(new)
    ok = np.isfinite(so) & np.isfinite(sn) & (so > 1.0)
    consistency = float(100.0 * (np.median(sn[ok] / so[ok]) - 1.0)) if ok.sum() >= MIN_SPREAD_DIMS else None

    stats = template_to_stats(old_template)
    scores = np.asarray([score_with_stats(v, stats, old_template["metric"]) for v in new], dtype=float)
    scores = scores[np.isfinite(scores)]
    shift = float(np.mean(scores) / old_template["threshold"]) if scores.size else None

    degraded = (speed is not None and speed >= DEGRADE_SPEED_PCT) or (
        consistency is not None and consistency >= DEGRADE_CONSISTENCY_PCT)
    better = (speed is not None and speed <= IMPROVE_SPEED_PCT) or (
        consistency is not None and consistency <= IMPROVE_CONSISTENCY_PCT)
    # protichodné signály (napr. rýchlejšie, ale menej stabilne) sa nepočítajú ako zlepšenie
    conflicting = (speed is not None and speed >= 3.0) or (consistency is not None and consistency >= 5.0)
    improved = better and not degraded and not conflicting
    verdict = "degraded" if degraded else "improved" if improved else "stable"
    return {
        "verdict": verdict,
        "label": VERDICT_LABELS[verdict],
        "speed_change_pct": speed,
        "consistency_change_pct": consistency,
        "shift_ratio": shift,
        "median_dd_old": dd_old if np.isfinite(dd_old) else None,
        "median_dd_new": dd_new if np.isfinite(dd_new) else None,
    }


def decide_threshold(old_threshold: float, rebuilt_threshold: float, max_growth: float, max_shrink: float) -> tuple[float, bool]:
    """Vráti (nový prah, bol obmedzený stropom/podlahou)."""
    ceiling = old_threshold * (1.0 + max_growth)
    floor = old_threshold * (1.0 - max_shrink)
    if rebuilt_threshold > ceiling:
        return ceiling, True
    if rebuilt_threshold < floor:
        return floor, False
    return rebuilt_threshold, False
