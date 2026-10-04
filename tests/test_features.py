import random

import pytest

from backend.features import BACKSPACE, FEATURE_COUNT, FEATURE_NAMES, extract_features

PHRASE_A = "dnes vecer cita moj brat novu knihu"
PHRASE_B = "stary kolega testuje novu siet pri rieke"


def type_events(text, dwell=80.0, gap=120.0, mistakes=(), rng=None):
    """Simuluje písanie; mistakes = indexy, kde sa najprv napíše zlý znak a opraví sa."""
    events, t = [], 100.0

    def press(key):
        nonlocal t
        d = dwell + (rng.uniform(-10, 10) if rng else 0)
        events.append({"key": key, "code": "x", "down": t, "up": t + d})
        t += d + gap

    for i, ch in enumerate(text):
        if i in mistakes:
            press("q" if ch != "q" else "w")
            press(BACKSPACE)
        press(ch)
    return events


def test_vector_length_and_names():
    features = extract_features(type_events(PHRASE_A), PHRASE_A)
    assert len(features) == FEATURE_COUNT == len(FEATURE_NAMES)


def test_dwell_values_and_missing_features():
    features = dict(zip(FEATURE_NAMES, extract_features(type_events(PHRASE_A), PHRASE_A)))
    assert features["dwell:e"] == 80.0
    assert features["dwell:q"] is None          # q sa vo vete nevyskytuje
    assert features["dd:ne"] is None or features["dd:ne"] == 200.0
    assert features["median_dd"] == 200.0
    assert features["median_ud"] == 120.0


def test_different_sentences_have_different_coverage():
    a = extract_features(type_events(PHRASE_A), PHRASE_A)
    b = extract_features(type_events(PHRASE_B), PHRASE_B)
    assert [v is None for v in a] != [v is None for v in b]


def test_corrections_are_allowed_and_excluded_from_pairs():
    clean = extract_features(type_events(PHRASE_A), PHRASE_A)
    fixed = extract_features(type_events(PHRASE_A, mistakes={5, 12}), PHRASE_A)
    assert len(fixed) == len(clean)
    assert fixed[FEATURE_NAMES.index("median_dwell")] == 80.0


def test_wrong_final_text_is_rejected():
    events = type_events(PHRASE_A)
    events[3]["key"] = "q"
    with pytest.raises(ValueError, match="nezhoduje"):
        extract_features(events, PHRASE_A)


def test_too_many_corrections_rejected():
    with pytest.raises(ValueError, match="opráv"):
        extract_features(type_events(PHRASE_A, mistakes=set(range(0, 30, 2))), PHRASE_A, max_corrections=5)


def test_backspace_on_empty_text_rejected():
    events = [{"key": BACKSPACE, "down": 0, "up": 50}] + type_events(PHRASE_A)
    for e in events[1:]:
        e["down"] += 200
        e["up"] += 200
    with pytest.raises(ValueError, match="prázdnom"):
        extract_features(events, PHRASE_A)


def test_rollover_negative_ud_is_allowed():
    events, t = [], 0.0
    for ch in PHRASE_A:
        events.append({"key": ch, "down": t, "up": t + 110.0})
        t += 90.0
    features = dict(zip(FEATURE_NAMES, extract_features(events, PHRASE_A)))
    assert features["median_ud"] == -20.0
    assert features["rollover_pct"] == 100.0


def test_invalid_key_rejected():
    events = type_events(PHRASE_A)
    events[0]["key"] = "A"
    with pytest.raises(ValueError, match="klávesa"):
        extract_features(events, PHRASE_A)
