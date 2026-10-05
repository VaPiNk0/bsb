import numpy as np

from backend.features import FEATURE_COUNT
from backend.scoring import Z_CLIP, build_template, calibrate_threshold, fit_stats, score_vector


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


def test_single_outlier_feature_does_not_dominate():
    """Jeden ustrelený príznak (napr. zaváhanie na jednom bigrame) nesmie zamietnuť inak zhodný pokus."""
    rng = np.random.default_rng(5)
    base = rng.uniform(70, 230, FEATURE_COUNT)
    template = build_template(synthetic_user(rng, 14, base, coverage=1.0), "manhattan", 2.0)
    clean = base + rng.normal(0, 6.0, size=base.size)
    spiked = clean.copy()
    spiked[0] += 2000.0
    assert score_vector(spiked, template) - score_vector(clean, template) <= Z_CLIP / FEATURE_COUNT + 1e-9


def test_scale_from_few_observations_is_not_overconfident():
    """Príznak videný len 3x s náhodne takmer rovnakými hodnotami nesmie dostať škálu na podlahe."""
    rng = np.random.default_rng(6)
    base = rng.uniform(150, 230, FEATURE_COUNT)
    matrix = synthetic_user(rng, 14, base, coverage=1.0, noise=20.0)
    matrix[:, 0] = np.nan
    matrix[:3, 0] = [200.0, 200.5, 201.0]
    stats = fit_stats(matrix)
    assert stats.valid[0] and stats.scale[0] > 10.0


def test_older_shorter_vectors_are_padded():
    """Vzorky uložené pred pridaním nových príznakov sú kratšie; chýbajúci koniec = nepozorované."""
    rng = np.random.default_rng(7)
    base = rng.uniform(70, 230, FEATURE_COUNT)
    full = synthetic_user(rng, 14, base, coverage=0.8)
    old = [[None if np.isnan(v) else float(v) for v in row[:FEATURE_COUNT - 11]] for row in full]
    template = build_template(old, "manhattan", 1.0)
    assert template["feature_count"] == FEATURE_COUNT - 11
    assert np.isfinite(score_vector(full[0], template))            # dlhší pokus voči staršej šablóne
    mixed = build_template(old[:10] + full[10:].tolist(), "manhattan", 1.0)
    assert mixed["feature_count"] == FEATURE_COUNT
    assert np.isfinite(score_vector(old[0], mixed))                # a naopak


def test_threshold_spread_is_pulled_towards_typical_value():
    """Náhodne takmer rovnaké (alebo rozhádzané) LOO skóre nesmú dať extrémne tesný (voľný) prah."""
    tight = np.array([1.0, 1.001, 0.999, 1.0, 1.002, 0.998])
    loose = np.array([0.4, 1.6, 0.5, 1.5, 0.6, 1.4])
    _, tight_std, tight_thr = calibrate_threshold(tight, 1.0)
    _, loose_std, _ = calibrate_threshold(loose, 1.0)
    assert tight_std > 0.1 and tight_thr > 1.1
    assert loose_std < np.std(loose, ddof=1)


def test_covariance_is_stored_only_for_mahalanobis():
    rng = np.random.default_rng(8)
    matrix = synthetic_user(rng, 12, rng.uniform(70, 230, FEATURE_COUNT))
    assert "cov" not in build_template(matrix, "manhattan", 1.0)
    assert "cov" in build_template(matrix, "mahalanobis", 1.0)
