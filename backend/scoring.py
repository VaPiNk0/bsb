"""Skórovanie pre vektory s chýbajúcimi hodnotami (NaN = príznak vo vete nebol).

Šablóna: medián a MAD pre každý príznak, kovariancia z-skóre so shrinkage.
Skóre sa počíta len cez príznaky, ktoré šablóna pozná (>= MIN_OBS pozorovaní)
a ktoré sa vyskytli v aktuálnom pokuse.

Dve poistky proti šumu pri malom počte pozorovaní (bez nich jediný príznak s náhodne
malou MAD dá |z| ~ 5-10, nafúkne rozptyl genuine skóre a tým aj prah):
* škála príznaku sa shrinkuje k typickej relatívnej škále ostatných príznakov,
* |z| sa orezáva na Z_CLIP, takže jeden výkyv nepreváži zvyšok vektora.

Prah je LOO priemer + k * rozptyl, pričom rozptyl z hŕstky LOO skóre sa ťahá k typickému
pomeru rozptyl/priemer (pozri calibrate_threshold).
"""
from __future__ import annotations

import math
import warnings
from dataclasses import dataclass
from typing import Any

import numpy as np

MIN_OBS = 2            # koľko enrollment pozorovaní musí príznak mať, aby sa použil
Z_CLIP = 3.0           # strop pre |z| jedného príznaku
SCALE_PRIOR_N = 4.0    # váha (v počte pozorovaní) typickej škály pri shrinkage
SCALE_PRIOR_MIN_OBS = 6  # typická škála sa odhaduje len z dobre pozorovaných príznakov
TEMPLATE_VERSION = 4
MIN_SCORED_DIMS = 15   # koľko príznakov musí pokus obsahovať, aby sa dal vyhodnotiť
# Smerodajná odchýlka z 10-20 LOO skóre je sama zašumená (pri n=12 má chybu ~20 %), takže
# niekto dostal náhodou voľný prah a iný prísny. Typický pomer std/mean genuine skóre je
# naprieč ľuďmi stabilný (medián 0.16 na Aalto 136M), preto sa k nemu odhad ťahá.
THRESHOLD_PRIOR_CV = 0.16
THRESHOLD_PRIOR_N = 10.0   # váha priora v počte LOO skóre


@dataclass
class TemplateStats:
    center: np.ndarray
    scale: np.ndarray
    cov: np.ndarray | None   # len pre Mahalanobis
    valid: np.ndarray
    kept_indices: list[int]


def to_matrix(vectors: Any) -> np.ndarray:
    """Zoznam vektorov -> 2D matica; kratšie (staršie) vektory sa doplnia NaN."""
    if isinstance(vectors, np.ndarray) and vectors.dtype != object:
        return vectors.astype(float, copy=False)
    rows = [np.asarray(row, dtype=float) for row in vectors]
    width = max((row.size for row in rows), default=0)
    out = np.full((len(rows), width), np.nan)
    for i, row in enumerate(rows):
        out[i, :row.size] = row
    return out


def _fit_width(vector: np.ndarray, width: int) -> np.ndarray:
    if vector.size >= width:
        return vector[:width]
    return np.concatenate([vector, np.full(width - vector.size, np.nan)])


def _center_scale(matrix: np.ndarray, floor: float) -> tuple[np.ndarray, np.ndarray]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        median = np.nanmedian(matrix, axis=0)
        mad = np.nanmedian(np.abs(matrix - median), axis=0)
    # 1.4826 približne prevádza MAD na ekvivalent sigma pre normálne rozdelenie.
    scale = 1.4826 * mad
    # MAD z 2-5 pozorovaní je veľmi nepresná; ťaháme ju k typickej relatívnej škále
    # (škála rastie s veľkosťou hodnoty: DD ~200 ms kolíše viac než dwell ~90 ms).
    counts = np.sum(~np.isnan(matrix), axis=0)
    magnitude = np.maximum(np.abs(median), 1.0)
    reliable = (counts >= SCALE_PRIOR_MIN_OBS) & np.isfinite(scale)
    if reliable.any():
        prior = float(np.median(scale[reliable] / magnitude[reliable])) * magnitude
        scale = np.sqrt((counts * scale**2 + SCALE_PRIOR_N * prior**2) / (counts + SCALE_PRIOR_N))
    return median, np.maximum(scale, floor)


def _row_scores(matrix: np.ndarray, center: np.ndarray, scale: np.ndarray, valid: np.ndarray) -> np.ndarray:
    z = np.minimum(np.abs((matrix - center) / scale), Z_CLIP)
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


def fit_stats(
    matrix: np.ndarray, floor: float = 5.0, z_limit: float = 3.5, shrinkage: float = 0.15, with_cov: bool = True,
) -> TemplateStats:
    x = to_matrix(matrix)
    if x.ndim != 2 or x.shape[0] < 1:
        raise ValueError("Matrix musí byť 2D a obsahovať aspoň jednu vzorku")

    valid = np.sum(~np.isnan(x), axis=0) >= MIN_OBS
    kept = _select_robust_rows(x, valid, floor, z_limit)
    clean = x[kept]
    valid = np.sum(~np.isnan(clean), axis=0) >= MIN_OBS
    center, scale = _center_scale(clean, floor)
    if not with_cov:
        return TemplateStats(center=center, scale=scale, cov=None, valid=valid, kept_indices=kept.tolist())

    z = np.clip((clean - center) / scale, -Z_CLIP, Z_CLIP)
    z = np.where(np.isfinite(z) & valid, z, 0.0)  # chýbajúce hodnoty = neutrálne
    dim = clean.shape[1]
    if clean.shape[0] < 2:
        cov = np.eye(dim)
    else:
        cov = np.atleast_2d(np.cov(z, rowvar=False, ddof=1))
    cov = (1.0 - shrinkage) * cov + shrinkage * np.eye(dim)
    return TemplateStats(center=center, scale=scale, cov=cov, valid=valid, kept_indices=kept.tolist())


def score_with_stats(vector: np.ndarray, stats: TemplateStats, metric: str) -> float:
    x = _fit_width(np.asarray(vector, dtype=float), stats.center.size)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        z = (x - stats.center) / stats.scale
    observed = stats.valid & np.isfinite(z)
    n = int(observed.sum())
    if n < MIN_SCORED_DIMS:
        return float("nan")
    zo = np.clip(z[observed], -Z_CLIP, Z_CLIP)
    metric = metric.lower()
    if metric == "manhattan":
        return float(np.mean(np.abs(zo)))
    if metric == "mahalanobis":
        if stats.cov is None:
            raise ValueError("Šablóna neobsahuje kovarianciu pre Mahalanobis")
        sub = stats.cov[np.ix_(observed, observed)]
        value = float(zo @ np.linalg.pinv(sub, rcond=1e-8) @ zo)
        # delenie n: skóre je porovnateľné medzi vetami s rôznym počtom príznakov
        return float(math.sqrt(max(value, 0.0) / n))
    raise ValueError(f"Neznáma metrika: {metric}")


def compute_loo_scores(matrix: np.ndarray, metric: str, floor: float = 5.0, z_limit: float = 3.5, shrinkage: float = 0.15) -> np.ndarray:
    x = to_matrix(matrix)
    if x.shape[0] < 3:
        raise ValueError("Na leave-one-out kalibráciu sú potrebné aspoň 3 vzorky")
    with_cov = metric.lower() == "mahalanobis"
    scores = []
    for idx in range(x.shape[0]):
        stats = fit_stats(np.delete(x, idx, axis=0), floor=floor, z_limit=z_limit, shrinkage=shrinkage, with_cov=with_cov)
        scores.append(score_with_stats(x[idx], stats, metric))
    scores_arr = np.asarray(scores, dtype=float)
    scores_arr = scores_arr[np.isfinite(scores_arr)]
    if scores_arr.size < 3:
        raise ValueError("Enrollment vzorky obsahujú príliš málo spoločných príznakov")
    return scores_arr


def calibrate_threshold(loo: np.ndarray, k: float) -> tuple[float, float, float]:
    """Z LOO skóre vráti (mean, std, prah = mean + k * std)."""
    loo = np.asarray(loo, dtype=float)
    mean = float(np.mean(loo))
    dof = max(loo.size - 1, 0)
    var = float(np.var(loo, ddof=1)) if dof else 0.0
    prior_var = (THRESHOLD_PRIOR_CV * mean) ** 2
    std = math.sqrt((dof * var + THRESHOLD_PRIOR_N * prior_var) / (dof + THRESHOLD_PRIOR_N))
    std = max(std, 1e-6)
    return mean, std, mean + float(k) * std


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
    x = to_matrix(matrix)
    if x.shape[0] < 3:
        raise ValueError("Na vytvorenie šablóny sú potrebné aspoň 3 vzorky")
    metric = metric.lower()
    stats = fit_stats(x, floor=floor, z_limit=z_limit, shrinkage=shrinkage, with_cov=metric == "mahalanobis")
    if int(stats.valid.sum()) < MIN_SCORED_DIMS:
        raise ValueError("Enrollment vzorky pokrývajú príliš málo príznakov, zopakuj registráciu")
    loo = compute_loo_scores(x, metric, floor=floor, z_limit=z_limit, shrinkage=shrinkage)
    mean, std, threshold = calibrate_threshold(loo, k)
    template = {
        "version": TEMPLATE_VERSION,
        "metric": metric,
        "k": float(k),
        "feature_count": int(x.shape[1]),
        "center": _jsonable(stats.center),
        "scale": _jsonable(stats.scale),
        "valid": [bool(v) for v in stats.valid],
        "kept_indices": stats.kept_indices,
        "enrollment_count": int(x.shape[0]),
        "usable_features": int(stats.valid.sum()),
        "loo_scores": loo.tolist(),
        "loo_mean": mean,
        "loo_std": std,
        "threshold": threshold,
    }
    if stats.cov is not None:   # 87x87 čísel; pri Manhattane by sa len zbytočne ukladali a parsovali
        template["cov"] = stats.cov.tolist()
    return template


def template_to_stats(template: dict[str, Any]) -> TemplateStats:
    return TemplateStats(
        center=_from_json(template["center"]),
        scale=_from_json(template["scale"]),
        cov=_from_json(template["cov"]) if template.get("cov") is not None else None,
        valid=np.asarray(template["valid"], dtype=bool),
        kept_indices=[int(i) for i in template.get("kept_indices", [])],
    )


def score_vector(vector: list[float | None] | np.ndarray, template: dict[str, Any], metric: str | None = None) -> float:
    chosen_metric = (metric or template["metric"]).lower()
    return score_with_stats(np.asarray(vector, dtype=float), template_to_stats(template), chosen_metric)
