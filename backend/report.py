"""Merateľné výsledky verifikácie z audit logu: tabuľky pre admin panel a export do referátu.

Počíta sa len z toho, čo systém pri pokuse naozaj rozhodol (uložené skóre, vtedajší prah
a výsledok), nič sa spätne neprepočítava. Do FAR/FRR idú iba pokusy, ktoré prešli heslom
a dostali biometrické skóre; ostatné (zlé heslo, zamknutý účet, replay) sa vykazujú zvlášť.
"""
from __future__ import annotations

import json
import math
from statistics import median
from typing import Any

from .features import FEATURE_NAMES

GENUINE = "genuine"
ATTACKS = ("impostor", "imitacia")
DWELL_INDEX = FEATURE_NAMES.index("median_dwell")
DD_INDEX = FEATURE_NAMES.index("median_dd")


def wilson_interval(errors: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95 % Wilsonov interval pre podiel. Pri malom n a podiele blízko 0 je spoľahlivejší než
    normálová aproximácia: aj 0 chýb z 12 pokusov dá hornú hranicu ~24 %, nie 0 %."""
    if n <= 0:
        return None
    p = errors / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def error_rate(errors: int, n: int) -> dict[str, Any]:
    interval = wilson_interval(errors, n)
    return {
        "n": n,
        "errors": errors,
        "rate": errors / n if n else None,
        "ci_low": interval[0] if interval else None,
        "ci_high": interval[1] if interval else None,
    }


def equal_error_rate(genuine: list[float], attack: list[float]) -> float | None:
    """EER nad pomerom skóre/prah: spoločný násobok všetkých per-user prahov, pri ktorom sa
    FAR a FRR vyrovnajú. Rovnaký postup ako eer_from_scores v evaluate.py."""
    if not genuine or not attack:
        return None
    best = None
    for t in sorted(set(genuine) | set(attack)):
        far = sum(1 for s in attack if s <= t) / len(attack)
        frr = sum(1 for s in genuine if s > t) / len(genuine)
        if best is None or abs(far - frr) < best[0]:
            best = (abs(far - frr), (far + frr) / 2)
    return best[1]


def _scored(row: Any) -> bool:
    return row["score"] is not None and bool(row["threshold"]) and not row["replay_detected"]


def _ratios(rows: list[Any]) -> list[float]:
    return [float(r["score"]) / float(r["threshold"]) for r in rows]


def _median(values: list[float]) -> float | None:
    return median(values) if values else None


def _scenario(name: str, rows: list[Any]) -> dict[str, Any]:
    """Chyba pri genuine pokuse je zamietnutie (FRR), pri útoku prijatie (FAR)."""
    accepted = sum(1 for r in rows if r["accepted"])
    genuine = name == GENUINE
    return {
        "scenario": name,
        "measure": "FRR" if genuine else "FAR",
        "accepted": accepted,
        "rejected": len(rows) - accepted,
        **error_rate(len(rows) - accepted if genuine else accepted, len(rows)),
        "median_ratio": _median(_ratios(rows)),
    }


def device_of(row: Any) -> str:
    """Typ zariadenia riadku; záznamy spred rozdelenia šablón vznikli na počítači."""
    try:
        return row["device"] or "pc"
    except (KeyError, IndexError):
        return "pc"


def typing_profiles(samples: list[Any]) -> dict[tuple[int, str], dict[str, Any]]:
    """Profil písania účtu na jednom type zariadenia z enrollment vzoriek: medián cez vety
    z mediánov dwell a DD vo vete."""
    by_user: dict[tuple[int, str], list[list[float | None]]] = {}
    for row in samples:
        by_user.setdefault((int(row["user_id"]), device_of(row)), []).append(json.loads(row["features_json"]))

    def column(vectors: list[list[float | None]], index: int) -> list[float]:
        return [float(v[index]) for v in vectors if index < len(v) and v[index] is not None]

    return {
        key: {
            "enrollment_n": len(vectors),
            "median_dwell_ms": _median(column(vectors, DWELL_INDEX)),
            "median_dd_ms": _median(column(vectors, DD_INDEX)),
        }
        for key, vectors in by_user.items()
    }


def _user_row(user, device, genuine, attacks, profiles, thresholds) -> dict[str, Any]:
    user_id = int(user["id"])
    own = [r for r in genuine if r["user_id"] == user_id and device_of(r) == device]
    against = [r for r in attacks if r["user_id"] == user_id and device_of(r) == device]
    profile = profiles.get((user_id, device), {})
    frr = error_rate(sum(1 for r in own if not r["accepted"]), len(own))
    far = error_rate(sum(1 for r in against if r["accepted"]), len(against))
    return {
        "username": user["username"],
        "device": device,
        "enrollment_n": profile.get("enrollment_n", 0),
        "median_dwell_ms": profile.get("median_dwell_ms"),
        "median_dd_ms": profile.get("median_dd_ms"),
        "threshold": thresholds.get((user_id, device)),
        "genuine_n": frr["n"], "genuine_rejected": frr["errors"], "frr": frr["rate"],
        "frr_ci_low": frr["ci_low"], "frr_ci_high": frr["ci_high"],
        "attack_n": far["n"], "attack_accepted": far["errors"], "far": far["rate"],
        "far_ci_low": far["ci_low"], "far_ci_high": far["ci_high"],
        "genuine_median_ratio": _median(_ratios(own)),
        "attack_median_ratio": _median(_ratios(against)),
    }


def build_report(
    attempts: list[Any], users: list[Any], samples: list[Any], thresholds: dict[tuple[int, str], float]
) -> dict[str, Any]:
    """`thresholds` je prah šablóny podľa (id účtu, zariadenie). Výsledky podľa účtu sú zvlášť
    pre každé zariadenie, lebo každé má vlastnú šablónu a iný rytmus písania."""
    scored = [r for r in attempts if _scored(r)]
    genuine = [r for r in scored if r["attempt_type"] == GENUINE]
    attacks = [r for r in scored if r["attempt_type"] in ATTACKS]

    scenarios = [_scenario(GENUINE, genuine)]
    scenarios += [_scenario(name, [r for r in attacks if r["attempt_type"] == name]) for name in ATTACKS]
    scenarios.append(_scenario("attacks_total", attacks))

    profiles = typing_profiles(samples)
    per_user = []
    for user in users:
        user_id = int(user["id"])
        used = {d for (uid, d) in list(profiles) + list(thresholds) if uid == user_id}
        used |= {device_of(r) for r in scored if r["user_id"] == user_id}
        for device in sorted(used) or ["pc"]:
            per_user.append(_user_row(user, device, genuine, attacks, profiles, thresholds))

    excluded: dict[str, int] = {}
    for row in attempts:
        if not _scored(row):
            reason = "replay" if row["replay_detected"] else (row["reason"] or "other")
            excluded[reason] = excluded.get(reason, 0) + 1

    return {
        "scored": len(scored),
        "excluded_total": len(attempts) - len(scored),
        "accounts": len({r["username"] for r in scored}),
        "testers": len({r["actor_label"] or r["username"] for r in scored}),
        # genuine pokus mimo test mode nemá potvrdené, kto písal: predpokladá sa vlastník účtu
        "genuine_labeled": sum(1 for r in genuine if r["test_mode"]),
        "eer": equal_error_rate(_ratios(genuine), _ratios(attacks)),
        "scenarios": scenarios,
        "users": per_user,
        "excluded": [{"reason": k, "n": v} for k, v in sorted(excluded.items(), key=lambda kv: -kv[1])],
    }


SCENARIO_COLUMNS = [
    "scenario", "measure", "n", "accepted", "rejected", "errors", "rate", "ci_low", "ci_high", "median_ratio",
]
USER_COLUMNS = [
    "username", "device", "enrollment_n", "median_dwell_ms", "median_dd_ms", "threshold",
    "genuine_n", "genuine_rejected", "frr", "frr_ci_low", "frr_ci_high",
    "attack_n", "attack_accepted", "far", "far_ci_low", "far_ci_high",
    "genuine_median_ratio", "attack_median_ratio",
]
ATTEMPT_COLUMNS = [
    "id", "created_at", "username", "device", "actor_label", "attempt_type", "test_mode",
    "score", "threshold", "accepted", "replay_detected", "reason",
]
