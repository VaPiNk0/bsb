"""Skórovanie pre vektory s chýbajúcimi hodnotami (NaN = príznak vo vete nebol).

Šablóna: medián a MAD pre každý príznak, kovariancia z-skóre so shrinkage.
Skóre sa počíta len cez príznaky, ktoré šablóna pozná (>= MIN_OBS pozorovaní)
a ktoré sa vyskytli v aktuálnom pokuse.
"""
from __future__ import annotations

import math
import warnings
from dataclasses import dataclass
from typing import Any

import numpy as np

MIN_OBS = 3            # koľko enrollment pozorovaní musí príznak mať, aby sa použil
MIN_SCORED_DIMS = 15   # koľko príznakov musí pokus obsahovať, aby sa dal vyhodnotiť


@dataclass
class TemplateStats:
    center: np.ndarray
    scale: np.ndarray
    cov: np.ndarray
    valid: np.ndarray
    kept_indices: list[int]


def _center_scale(matrix: np.ndarray, floor: float) -> tuple[np.ndarray, np.ndarray]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        median = np.nanmedian(matrix, axis=0)
        mad = np.nanmedian(np.abs(matrix - median), axis=0)
    # 1.4826 približne prevádza MAD na ekvivalent sigma pre normálne rozdelenie.
    return median, np.maximum(1.4826 * mad, floor)


def _row_scores(matrix: np.ndarray, center: np.ndarray, scale: np.ndarray, valid: np.ndarray) -> np.ndarray:
    z = np.abs((matrix - center) / scale)
    z[:, ~valid] = np.nan
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        scores = np.nanmean(z, axis=1)
    return np.where(np.isfinite(scores), scores, np.nanmedian(scores) if np.isfinite(scores).any() else 0.0)


def _select_robust_rows(matrix: np.ndarray, valid: np.ndarray, floor: float, z_limit: float) -> np.ndarray:
    if matrix.shape[0] <= 3:
        return np.arange(matrix.shape[0])
    center, scale = _center_scale(matrix, floor)
    row_score = _row_scores(matrix, center, scale, valid)
    score_med = float(np.median(row_score))
    score_mad = float(1.4826 * np.median(np.abs(row_score - score_med)))
    cutoff = score_med + z_limit * max(score_mad, 0.05)
    kept = np.flatnonzero(row_score <= cutoff)
    minimum = max(3, matrix.shape[0] // 2)
    if kept.size < minimum:
        kept = np.argsort(row_score)[:minimum]
    return kept


def fit_stats(matrix: np.ndarray, floor: float = 5.0, z_limit: float = 3.5, shrinkage: float = 0.15) -> TemplateStats:
    x = np.asarray(matrix, dtype=float)
    if x.ndim != 2 or x.shape[0] < 1:
        raise ValueError("Matrix musí byť 2D a obsahovať aspoň jednu vzorku")

    valid = np.sum(~np.isnan(x), axis=0) >= MIN_OBS
    kept = _select_robust_rows(x, valid, floor, z_limit)
    clean = x[kept]
    valid = np.sum(~np.isnan(clean), axis=0) >= MIN_OBS
    center, scale = _center_scale(clean, floor)

    z = (clean - center) / scale
    z = np.where(np.isfinite(z) & valid, z, 0.0)  # chýbajúce hodnoty = neutrálne
    dim = clean.shape[1]
    if clean.shape[0] < 2:
        cov = np.eye(dim)
    else:
        cov = np.atleast_2d(np.cov(z, rowvar=False, ddof=1))
    cov = (1.0 - shrinkage) * cov + shrinkage * np.eye(dim)
    return TemplateStats(center=center, scale=scale, cov=cov, valid=valid, kept_indices=kept.tolist())


def score_with_stats(vector: np.ndarray, stats: TemplateStats, metric: str) -> float:
    x = np.asarray(vector, dtype=float)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        z = (x - stats.center) / stats.scale
    observed = stats.valid & np.isfinite(z)
    n = int(observed.sum())
    if n < MIN_SCORED_DIMS:
        return float("nan")
    zo = z[observed]
    metric = metric.lower()
    if metric == "manhattan":
        return float(np.mean(np.abs(zo)))
    if metric == "mahalanobis":
        sub = stats.cov[np.ix_(observed, observed)]
        value = float(zo @ np.linalg.pinv(sub, rcond=1e-8) @ zo)
        # delenie n: skóre je porovnateľné medzi vetami s rôznym počtom príznakov
        return float(math.sqrt(max(value, 0.0) / n))
    raise ValueError(f"Neznáma metrika: {metric}")


def compute_loo_scores(matrix: np.ndarray, metric: str, floor: float = 5.0, z_limit: float = 3.5, shrinkage: float = 0.15) -> np.ndarray:
    x = np.asarray(matrix, dtype=float)
    if x.shape[0] < 3:
        raise ValueError("Na leave-one-out kalibráciu sú potrebné aspoň 3 vzorky")
    scores = []
    for idx in range(x.shape[0]):
        stats = fit_stats(np.delete(x, idx, axis=0), floor=floor, z_limit=z_limit, shrinkage=shrinkage)
        scores.append(score_with_stats(x[idx], stats, metric))
    scores_arr = np.asarray(scores, dtype=float)
    scores_arr = scores_arr[np.isfinite(scores_arr)]
    if scores_arr.size < 3:
        raise ValueError("Enrollment vzorky obsahujú príliš málo spoločných príznakov")
    return scores_arr


def _jsonable(array: np.ndarray) -> list:
    if array.ndim == 1:
        return [None if not np.isfinite(v) else float(v) for v in array]
    return array.tolist()


def _from_json(values: list, dtype=float) -> np.ndarray:
    if values and isinstance(values[0], list):
        return np.asarray(values, dtype=dtype)
    return np.asarray([np.nan if v is None else v for v in values], dtype=dtype)


def build_template(
    matrix: np.ndarray,
    metric: str,
    k: float,
    floor: float = 5.0,
    z_limit: float = 3.5,
    shrinkage: float = 0.15,
) -> dict[str, Any]:
    x = np.asarray(matrix, dtype=float)
    if x.shape[0] < 3:
        raise ValueError("Na vytvorenie šablóny sú potrebné aspoň 3 vzorky")
    stats = fit_stats(x, floor=floor, z_limit=z_limit, shrinkage=shrinkage)
    if int(stats.valid.sum()) < MIN_SCORED_DIMS:
        raise ValueError("Enrollment vzorky pokrývajú príliš málo príznakov, zopakuj registráciu")
    loo = compute_loo_scores(x, metric, floor=floor, z_limit=z_limit, shrinkage=shrinkage)
    mean = float(np.mean(loo))
    std = float(np.std(loo, ddof=1)) if loo.size > 1 else 0.0
    # Ak sú enrollment pokusy extrémne konzistentné, nulový prah by bol nepraktický.
    std = max(std, max(mean * 0.02, 1e-6))
    return {
        "version": 2,
        "metric": metric.lower(),
        "k": float(k),
        "feature_count": int(x.shape[1]),
        "center": _jsonable(stats.center),
        "scale": _jsonable(stats.scale),
        "cov": stats.cov.tolist(),
        "valid": [bool(v) for v in stats.valid],
        "kept_indices": stats.kept_indices,
        "enrollment_count": int(x.shape[0]),
        "usable_features": int(stats.valid.sum()),
        "loo_scores": loo.tolist(),
        "loo_mean": mean,
        "loo_std": std,
        "threshold": mean + float(k) * std,
    }


def template_to_stats(template: dict[str, Any]) -> TemplateStats:
    return TemplateStats(
        center=_from_json(template["center"]),
        scale=_from_json(template["scale"]),
        cov=_from_json(template["cov"]),
        valid=np.asarray(template["valid"], dtype=bool),
        kept_indices=[int(i) for i in template.get("kept_indices", [])],
    )


def score_vector(vector: list[float | None] | np.ndarray, template: dict[str, Any], metric: str | None = None) -> float:
    chosen_metric = (metric or template["metric"]).lower()
    return score_with_stats(np.asarray(vector, dtype=float), template_to_stats(template), chosen_metric)
