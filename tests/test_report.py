import json

import pytest

from backend.features import FEATURE_COUNT
from backend.report import DD_INDEX, DWELL_INDEX, build_report, equal_error_rate, wilson_interval


def attempt(kind, score, accepted, user_id=1, username="P01", actor=None, **extra):
    return {
        "user_id": user_id, "username": username, "actor_label": actor or username, "attempt_type": kind,
        "score": score, "threshold": 1.0 if score is not None else None, "accepted": 1 if accepted else 0,
        "replay_detected": 0, "reason": None, "test_mode": 1, **extra,
    }


def test_wilson_interval_is_not_degenerate_for_zero_errors():
    low, high = wilson_interval(0, 12)
    assert low == 0.0 and 0.2 < high < 0.3
    low, high = wilson_interval(5, 10)
    assert low == pytest.approx(0.237, abs=0.001) and high == pytest.approx(0.763, abs=0.001)
    assert wilson_interval(0, 0) is None


def test_equal_error_rate():
    assert equal_error_rate([0.5, 0.6, 0.7], [1.5, 1.8]) == 0.0          # dokonale oddelené
    assert equal_error_rate([0.5, 0.9, 1.2, 1.6], [0.8, 1.4, 1.7, 2.0]) == 0.25
    assert equal_error_rate([0.5], []) is None


def test_report_counts_only_scored_attempts():
    attempts = (
        [attempt("genuine", 0.8, True)] * 9 + [attempt("genuine", 1.3, False)]
        + [attempt("impostor", 1.9, False, actor="P02")] * 3 + [attempt("impostor", 0.9, True, actor="P02")]
        + [attempt("imitacia", 1.4, False, actor="P03")] * 2
        + [attempt("genuine", None, False, reason="password_failed")] * 2
        + [attempt("genuine", 0.8, False, replay_detected=1)]
    )
    vector = [None] * FEATURE_COUNT
    vector[DWELL_INDEX], vector[DD_INDEX] = 95.0, 180.0
    samples = [{"user_id": 1, "features_json": json.dumps(vector)}] * 12
    report = build_report(attempts, [{"id": 1, "username": "P01"}], samples, {1: 1.25})

    by_name = {s["scenario"]: s for s in report["scenarios"]}
    assert (by_name["genuine"]["n"], by_name["genuine"]["errors"], by_name["genuine"]["rate"]) == (10, 1, 0.1)
    assert (by_name["impostor"]["n"], by_name["impostor"]["rate"]) == (4, 0.25)
    assert (by_name["imitacia"]["n"], by_name["imitacia"]["rate"]) == (2, 0.0)
    assert (by_name["attacks_total"]["n"], by_name["attacks_total"]["errors"]) == (6, 1)
    assert report["scored"] == 16 and report["excluded_total"] == 3
    assert {e["reason"]: e["n"] for e in report["excluded"]} == {"password_failed": 2, "replay": 1}
    assert report["testers"] == 3 and report["genuine_labeled"] == 10

    user = report["users"][0]
    assert (user["enrollment_n"], user["median_dwell_ms"], user["median_dd_ms"], user["threshold"]) == (12, 95.0, 180.0, 1.25)
    assert (user["frr"], user["far"]) == (0.1, 1 / 6)
    assert user["genuine_median_ratio"] == 0.8


def test_report_without_data_has_no_rates():
    report = build_report([], [], [], {})
    assert all(s["n"] == 0 and s["rate"] is None for s in report["scenarios"])
    assert report["eer"] is None
