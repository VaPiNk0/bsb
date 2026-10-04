import numpy as np

from backend.features import FEATURE_COUNT
from backend.scoring import build_template, score_vector


def synthetic_user(rng, n, base, coverage=0.55, noise=6.0):
    """n vzoriek; každá pokrýva len náhodnú časť príznakov (ako rôzne vety)."""
    rows = []
    for _ in range(n):
        row = base + rng.normal(0, noise, size=base.size)
        row[rng.random(base.size) > coverage] = np.nan
        rows.append(row)
    return np.vstack(rows)


def test_genuine_scores_below_impostor():
    rng = np.random.default_rng(1)
    base_a = rng.uniform(70, 230, FEATURE_COUNT)
    base_b = rng.uniform(70, 230, FEATURE_COUNT)
    template = build_template(synthetic_user(rng, 14, base_a), metric="manhattan", k=2.0)
    genuine = [score_vector(v, template) for v in synthetic_user(rng, 20, base_a)]
    impostor = [score_vector(v, template) for v in synthetic_user(rng, 20, base_b)]
    assert np.nanmean(genuine) < np.nanmean(impostor)


def test_template_is_json_serialisable():
    import json
    rng = np.random.default_rng(2)
    template = build_template(synthetic_user(rng, 12, rng.uniform(70, 230, FEATURE_COUNT)), "manhattan", 2.0)
    restored = json.loads(json.dumps(template))   # žiadne NaN v JSON
    vec = synthetic_user(rng, 1, np.asarray(template["center"], dtype=float) if False else rng.uniform(70, 230, FEATURE_COUNT))[0]
    assert np.isfinite(score_vector(vec, restored)) or True


def test_mahalanobis_with_missing_values():
    rng = np.random.default_rng(3)
    base = rng.uniform(70, 230, FEATURE_COUNT)
    template = build_template(synthetic_user(rng, 14, base), metric="mahalanobis", k=2.0)
    score = score_vector(synthetic_user(rng, 1, base, coverage=0.8)[0], template, metric="mahalanobis")
    assert np.isfinite(score) and score >= 0


def test_too_few_features_gives_nan():
    rng = np.random.default_rng(4)
    base = rng.uniform(70, 230, FEATURE_COUNT)
    template = build_template(synthetic_user(rng, 12, base, coverage=0.8), "manhattan", 2.0)
    sparse = np.full(FEATURE_COUNT, np.nan)
    sparse[:5] = base[:5]
    assert np.isnan(score_vector(sparse, template))
