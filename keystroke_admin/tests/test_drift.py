import numpy as np

from backend.drift import analyze_drift, decide_threshold
from backend.features import FEATURE_COUNT, FEATURE_NAMES
from backend.scoring import build_template

IDX_DD = FEATURE_NAMES.index("median_dd")


def users_matrix(rng, n, base, noise=8.0):
    m = base + rng.normal(0, noise, size=(n, base.size))
    m[rng.random(m.shape) > 0.6] = np.nan
    m[:, IDX_DD] = base[IDX_DD] + rng.normal(0, noise, n)
    return m


def setup(seed=0):
    rng = np.random.default_rng(seed)
    base = rng.uniform(70, 230, FEATURE_COUNT)
    old = users_matrix(rng, 16, base)
    return rng, base, old, build_template(old, "manhattan", 2.0)


def test_same_typing_is_stable():
    rng, base, old, tpl = setup(1)
    report = analyze_drift(old, users_matrix(rng, 6, base), tpl)
    assert report["verdict"] == "stable"


def test_faster_typing_is_improved():
    rng, base, old, tpl = setup(2)
    faster = base * 0.88
    report = analyze_drift(old, users_matrix(rng, 6, faster), tpl)
    assert report["verdict"] == "improved"
    assert report["speed_change_pct"] < -5


def test_slower_typing_is_degraded():
    rng, base, old, tpl = setup(3)
    report = analyze_drift(old, users_matrix(rng, 6, base * 1.2), tpl)
    assert report["verdict"] == "degraded"
    assert report["speed_change_pct"] > 10


def test_less_consistent_typing_is_degraded():
    rng, base, old, tpl = setup(4)
    report = analyze_drift(old, users_matrix(rng, 8, base, noise=30.0), tpl)
    assert report["verdict"] == "degraded"
    assert report["consistency_change_pct"] > 15


def test_threshold_growth_is_capped_and_shrink_is_limited():
    assert decide_threshold(2.0, 4.0, 0.25, 0.30) == (2.5, True)
    assert decide_threshold(2.0, 1.0, 0.25, 0.30) == (1.4, False)
    assert decide_threshold(2.0, 2.2, 0.25, 0.30) == (2.2, False)
