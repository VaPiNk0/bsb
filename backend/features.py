"""Extrakcia príznakov z časovania kláves pre meniaci sa text.

Veta sa pri každom pokuse mení, preto vektor nie je viazaný na pozíciu znaku, ale na
*klávesu* (dwell pre každý znak) a na *frekventované dvojice znakov* (DD pre bigramy).
Čo veta neobsahuje, je ``None``; skórovanie takéto príznaky preskočí.

Opravy (Backspace) sú povolené. Do príznakov idú len stlačenia, ktoré v texte zostali
a ktoré nasledovali hneď po sebe (bez opravy medzi nimi).
"""
from __future__ import annotations

from statistics import median
from typing import Any

LETTERS = "abcdefghijklmnopqrstuvwxyz"
ALPHABET = LETTERS + " "
BACKSPACE = "Backspace"

# Frekventované dvojice znakov (vrátane medzery), pre ktoré sa meria čas medzi stlačeniami.
BIGRAMS = [
    "ne", "to", "na", "ov", "en", "ro", "ra", "ie", "ni", "st", "po", "ta", "ko", "pr",
    "me", "re", "ti", "ho", "te", "ch", "ak", "al", "ar", "an", "je", "ej", "om", "ou",
    "ve", "va", "mi", "sa", "at", "ed", "e ", "a ", "o ", "i ", "m ", " s", " n", " p",
    " v", " z",
]
GLOBALS = ["median_dwell", "median_dd", "median_ud", "p90_dd", "rollover_pct"]

FEATURE_NAMES = (
    [f"dwell:{c}" for c in ALPHABET]
    + [f"dd:{b}" for b in BIGRAMS]
    + GLOBALS
)
FEATURE_COUNT = len(FEATURE_NAMES)

MAX_DWELL_MS = 2000.0   # držanie klávesy dlhšie než 2 s nie je písanie
MAX_GAP_MS = 15000.0    # pauza dlhšia než 15 s = prerušený pokus
MIN_CLEAN_PAIRS = 10    # minimum čistých po sebe idúcich dvojíc pre globálne príznaky


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def parse_events(events: list[dict[str, Any]], phrase: str, max_corrections: int) -> list[tuple[str, float, float]]:
    if not isinstance(events, list) or not events:
        raise ValueError("Chýbajú údaje o stlačených klávesoch")
    if len(events) > len(phrase) * 3 + 10:
        raise ValueError("Príliš veľa stlačení kláves")

    parsed: list[tuple[str, float, float]] = []
    previous_down = 0.0
    for idx, event in enumerate(events):
        if not isinstance(event, dict):
            raise ValueError(f"Neplatný záznam na pozícii {idx}")
        key = event.get("key")
        if key != BACKSPACE and (not isinstance(key, str) or len(key) != 1 or key not in ALPHABET):
            raise ValueError(f"Neplatná klávesa na pozícii {idx}")
        try:
            down = float(event["down"])
            up = float(event["up"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Neplatné časové údaje na pozícii {idx}") from exc
        if not (0 <= down <= up) or up - down > MAX_DWELL_MS:
            raise ValueError(f"Neplatné down/up na pozícii {idx}")
        if down < previous_down or (idx and down - previous_down > MAX_GAP_MS):
            raise ValueError("Časová postupnosť kláves je nekonzistentná")
        previous_down = down
        parsed.append((key, down, up))

    stack: list[int] = []
    corrections = 0
    for idx, (key, _, _) in enumerate(parsed):
        if key == BACKSPACE:
            if not stack:
                raise ValueError("Backspace na prázdnom texte")
            stack.pop()
            corrections += 1
        else:
            stack.append(idx)
    if corrections > max_corrections:
        raise ValueError("Príliš veľa opráv")
    if "".join(parsed[i][0] for i in stack) != phrase:
        raise ValueError("Napísaný text sa nezhoduje so zadanou vetou")
    return parsed


def extract_features(
    events: list[dict[str, Any]], phrase: str, max_corrections: int = 15
) -> list[float | None]:
    parsed = parse_events(events, phrase, max_corrections)

    survivors: list[int] = []
    for idx, (key, _, _) in enumerate(parsed):
        if key == BACKSPACE:
            survivors.pop()
        else:
            survivors.append(idx)

    dwell_by_key: dict[str, list[float]] = {}
    dd_by_bigram: dict[str, list[float]] = {}
    dwell_all: list[float] = []
    dd_all: list[float] = []
    ud_all: list[float] = []

    for pos, idx in enumerate(survivors):
        key, down, up = parsed[idx]
        dwell = up - down
        dwell_by_key.setdefault(key, []).append(dwell)
        dwell_all.append(dwell)
        if pos == 0:
            continue
        prev_idx = survivors[pos - 1]
        if prev_idx != idx - 1:
            continue  # medzi nimi bola oprava, prechod nie je čistý
        prev_key, prev_down, prev_up = parsed[prev_idx]
        dd = down - prev_down
        ud = down - prev_up
        dd_all.append(dd)
        ud_all.append(ud)
        dd_by_bigram.setdefault(prev_key + key, []).append(dd)

    if len(dd_all) < MIN_CLEAN_PAIRS:
        raise ValueError("Príliš málo čistých prechodov medzi klávesami (veľa opráv)")

    vector: list[float | None] = []
    for c in ALPHABET:
        values = dwell_by_key.get(c)
        vector.append(median(values) if values else None)
    for b in BIGRAMS:
        values = dd_by_bigram.get(b)
        vector.append(median(values) if values else None)
    vector += [
        median(dwell_all),
        median(dd_all),
        median(ud_all),
        _percentile(dd_all, 0.9),
        100.0 * sum(1 for v in ud_all if v < 0) / len(ud_all),
    ]
    if len(vector) != FEATURE_COUNT:
        raise ValueError("Nekonzistentná dĺžka vektora")
    return [None if v is None else round(float(v), 4) for v in vector]
