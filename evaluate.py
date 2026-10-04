from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import norm

from backend.scoring import compute_loo_scores, fit_stats, score_with_stats


DEFAULT_METRICS = ["manhattan", "mahalanobis"]
DEFAULT_ENROLLMENT_COUNTS = [5, 10, 15]
DEFAULT_KS = [1.0, 1.5, 2.0, 2.5, 3.0]


def load_data(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    users = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    samples = conn.execute("SELECT * FROM enrollment_samples ORDER BY user_id, sample_index").fetchall()
    attempts = conn.execute(
        "SELECT * FROM auth_attempts WHERE score IS NOT NULL AND feature_json IS NOT NULL ORDER BY id"
    ).fetchall()
    conn.close()
    return users, samples, attempts


def vectors_by_user(samples):
    result = {}
    for row in samples:
        result.setdefault(int(row["user_id"]), []).append(json.loads(row["features_json"]))
    return result


def attempts_to_matrix(attempts):
    rows = []
    for row in attempts:
        rows.append({
            "user_id": int(row["user_id"]),
            "username": row["username"],
            "attempt_type": row["attempt_type"],
            "actor_label": row["actor_label"],
            "features": np.asarray(json.loads(row["feature_json"]), dtype=float),
        })
    return rows



def fit_user(vectors, metric):
    matrix = np.asarray(vectors, dtype=float)
    stats = fit_stats(matrix)
    loo = compute_loo_scores(matrix, metric)
    return stats, loo


def threshold_from_calibration(calibration, k):
    loo = calibration[1]
    mean = float(np.mean(loo))
    std = float(np.std(loo, ddof=1)) if loo.size > 1 else 0.0
    std = max(std, max(mean * 0.02, 1e-6))
    return mean + float(k) * std


def score_attempts(v_by_user, attempt_rows, metric, n_enrollment, calibrations=None):
    if calibrations is None:
        calibrations = {}
        for user_id, vectors in v_by_user.items():
            if len(vectors) >= n_enrollment:
                calibrations[user_id] = fit_user(vectors[:n_enrollment], metric)

    scored = []
    for row in attempt_rows:
        calibration = calibrations.get(row["user_id"])
        if calibration is None:
            continue
        label = 1 if row["attempt_type"] == "genuine" else 0
        stats = calibration[0]
        score = score_with_stats(row["features"], stats, metric)
        if not np.isfinite(score):
            continue  # pokus mal príliš málo použiteľných príznakov
        scored.append((score, label, row["attempt_type"], row["user_id"]))
    return scored, calibrations

def eer_from_scores(scores: np.ndarray, labels: np.ndarray):
    genuine = scores[labels == 1]
    impostor = scores[labels == 0]
    if genuine.size == 0 or impostor.size == 0:
        return math.nan, math.nan
    thresholds = np.unique(scores)
    far_values = np.asarray([np.mean(impostor <= t) for t in thresholds])
    frr_values = np.asarray([np.mean(genuine > t) for t in thresholds])
    idx = int(np.argmin(np.abs(far_values - frr_values)))
    return float((far_values[idx] + frr_values[idx]) / 2.0), float(thresholds[idx])

def roc_curve_manual(scores: np.ndarray, labels: np.ndarray):
    genuine = scores[labels == 1]
    impostor = scores[labels == 0]
    if genuine.size == 0 or impostor.size == 0:
        return np.array([]), np.array([])
    thresholds = np.r_[np.nextafter(scores.max(), np.inf), np.unique(np.sort(scores))[::-1], np.nextafter(scores.min(), -np.inf)]
    fpr, tpr = [], []
    for t in thresholds:
        fpr.append(float(np.mean(impostor <= t)))
        tpr.append(float(np.mean(genuine <= t)))
    order = np.argsort(fpr)
    return np.asarray(fpr)[order], np.asarray(tpr)[order]

def det_points(scores: np.ndarray, labels: np.ndarray):
    fpr, tpr = roc_curve_manual(scores, labels)
    if fpr.size == 0:
        return fpr, fpr
    frr = 1.0 - tpr
    eps = 1e-6
    return norm.ppf(np.clip(fpr, eps, 1 - eps)), norm.ppf(np.clip(frr, eps, 1 - eps))

def write_csv(path: Path, fieldnames, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="Vyhodnotenie keystroke dynamics z SQLite DB")
    parser.add_argument("--db", default="data/keystroke.db")
    parser.add_argument("--out", default="results")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    users, samples, attempts = load_data(args.db)
    v_by_user = vectors_by_user(samples)
    attempt_rows = attempts_to_matrix(attempts)

    if not attempt_rows:
        print("DB zatiaľ neobsahuje vyhodnotiteľné biometrické pokusy.")
        return

    summary_rows = []
    scenario_rows = []
    per_user_rows = []
    op_rows = []
    k_rows = []

    # Kalibrácie sú cacheované: LOO Mahalanobis je najdrahšia časť výpočtu.
    calibration_cache = {}
    scored_cache = {}
    for metric in DEFAULT_METRICS:
        calibration_cache[metric] = {}
        scored_cache[metric] = {}
        for n in DEFAULT_ENROLLMENT_COUNTS:
            calibrations = {}
            for user_id, vectors in v_by_user.items():
                if len(vectors) >= n:
                    calibrations[user_id] = fit_user(vectors[:n], metric)
            calibration_cache[metric][n] = calibrations
            scored_cache[metric][n], _ = score_attempts(v_by_user, attempt_rows, metric, n, calibrations=calibrations)

    for metric in DEFAULT_METRICS:
        for n in DEFAULT_ENROLLMENT_COUNTS:
            calibrations = calibration_cache[metric][n]
            scored = scored_cache[metric][n]
            if not scored:
                continue
            scores = np.asarray([x[0] for x in scored], dtype=float)
            labels = np.asarray([x[1] for x in scored], dtype=int)
            eer, _ = eer_from_scores(scores, labels)

            # Nasadený stav: per-user threshold z LOO + k=2.
            deployed_far = []
            deployed_frr = []
            for score, label, _, uid in scored:
                threshold = threshold_from_calibration(calibrations[uid], 2.0)
                accepted = score <= threshold
                if label == 0:
                    deployed_far.append(accepted)
                else:
                    deployed_frr.append(not accepted)
            far = float(np.mean(deployed_far)) if deployed_far else math.nan
            frr = float(np.mean(deployed_frr)) if deployed_frr else math.nan
            summary_rows.append({
                "metric": metric, "enrollment_n": n, "k": 2.0,
                "FAR": far, "FRR": frr, "EER": eer,
                "n_genuine": int(np.sum(labels == 1)), "n_impostor": int(np.sum(labels == 0)),
            })

            # Sweep parametra k.
            for k_value in DEFAULT_KS:
                fa, fr = [], []
                for score, label, _, uid in scored:
                    accepted = score <= threshold_from_calibration(calibrations[uid], k_value)
                    if label == 0:
                        fa.append(accepted)
                    else:
                        fr.append(not accepted)
                if fa and fr:
                    k_rows.append({
                        "metric": metric, "enrollment_n": n, "k": float(k_value),
                        "FAR": float(np.mean(fa)), "FRR": float(np.mean(fr)),
                        "n_genuine": len(fr), "n_impostor": len(fa),
                    })

            # Scenario comparison: genuine proti konkrétnemu typu útoku.
            for scenario in ["impostor", "imitacia"]:
                filtered = [(s, l) for s, l, typ, _ in scored if typ == "genuine" or typ == scenario]
                if not filtered:
                    continue
                s_arr = np.asarray([x[0] for x in filtered])
                l_arr = np.asarray([x[1] for x in filtered])
                sc_eer, _ = eer_from_scores(s_arr, l_arr)
                scenario_rows.append({
                    "metric": metric, "enrollment_n": n, "scenario": scenario,
                    "EER": sc_eer, "n_genuine": int(np.sum(l_arr == 1)), "n_attack": int(np.sum(l_arr == 0)),
                })

            # Per-user EER.
            for user in users:
                uid = int(user["id"])
                user_scored = [(s, l) for s, l, _, u in scored if u == uid]
                if not user_scored:
                    continue
                s_arr = np.asarray([x[0] for x in user_scored])
                l_arr = np.asarray([x[1] for x in user_scored])
                ue, _ = eer_from_scores(s_arr, l_arr)
                per_user_rows.append({
                    "username": user["username"], "user_id": uid, "metric": metric,
                    "enrollment_n": n, "EER": ue,
                    "n_genuine": int(np.sum(l_arr == 1)), "n_impostor": int(np.sum(l_arr == 0)),
                })

            # Empirické operačné body: nájdeme najvyššie k, ktoré dosiahne FAR <= cieľ.
            for target_far in [0.01, 0.05]:
                candidate = None
                for k in np.arange(0.1, 6.01, 0.1):
                    fa, fr = [], []
                    for score, label, _, uid in scored:
                        accepted = score <= threshold_from_calibration(calibrations[uid], float(k))
                        if label == 0:
                            fa.append(accepted)
                        else:
                            fr.append(not accepted)
                    if not fa or not fr:
                        continue
                    far_k = float(np.mean(fa)); frr_k = float(np.mean(fr))
                    if far_k <= target_far:
                        candidate = (float(k), far_k, frr_k)
                if candidate is None:
                    op_rows.append({"metric": metric, "enrollment_n": n, "target_FAR": target_far, "k": math.nan, "empirical_FAR": math.nan, "FRR": math.nan})
                else:
                    op_rows.append({"metric": metric, "enrollment_n": n, "target_FAR": target_far, "k": candidate[0], "empirical_FAR": candidate[1], "FRR": candidate[2]})

        # Globálna ROC/DET na 10 enrollment vzorkách.
        scored = scored_cache[metric].get(10, [])
        if scored:
            scores = np.asarray([x[0] for x in scored])
            labels = np.asarray([x[1] for x in scored])
            fpr, tpr = roc_curve_manual(scores, labels)
            plt.figure(figsize=(7, 5))
            plt.plot(fpr, tpr, marker=".", linewidth=1.4)
            plt.plot([0, 1], [0, 1], linestyle="--", linewidth=1)
            plt.xscale("log")
            plt.xlim(left=1e-3, right=1)
            plt.xlabel("FAR (False Accept Rate)")
            plt.ylabel("TAR (True Accept Rate)")
            plt.title(f"ROC – {metric}, enrollment=10")
            plt.grid(True, which="both", alpha=0.25)
            plt.tight_layout()
            plt.savefig(out / f"roc_{metric}_global.png", dpi=180)
            plt.close()

            x, y = det_points(scores, labels)
            plt.figure(figsize=(7, 5))
            plt.plot(x, y, marker=".", linewidth=1.4)
            plt.xlabel("False Accept Rate (normal deviate)")
            plt.ylabel("False Reject Rate (normal deviate)")
            plt.title(f"DET – {metric}, enrollment=10")
            plt.grid(True, alpha=0.25)
            plt.tight_layout()
            plt.savefig(out / f"det_{metric}_global.png", dpi=180)
            plt.close()

        points = [r for r in k_rows if r["metric"] == metric and r["enrollment_n"] == 10]
        if points:
            plt.figure(figsize=(7, 5))
            plt.plot([r["k"] for r in points], [r["FAR"] for r in points], marker="o", label="FAR")
            plt.plot([r["k"] for r in points], [r["FRR"] for r in points], marker="o", label="FRR")
            plt.xlabel("k")
            plt.ylabel("Rate")
            plt.ylim(0, 1)
            plt.title(f"Vplyv k – {metric}, enrollment=10")
            plt.grid(True, alpha=0.25)
            plt.legend()
            plt.tight_layout()
            plt.savefig(out / f"k_effect_{metric}_global.png", dpi=180)
            plt.close()

    write_csv(out / "summary_metrics.csv",
              ["metric", "enrollment_n", "k", "FAR", "FRR", "EER", "n_genuine", "n_impostor"],
              summary_rows)
    write_csv(out / "k_sweep.csv",
              ["metric", "enrollment_n", "k", "FAR", "FRR", "n_genuine", "n_impostor"],
              k_rows)
    write_csv(out / "scenario_comparison.csv",
              ["metric", "enrollment_n", "scenario", "EER", "n_genuine", "n_attack"],
              scenario_rows)
    write_csv(out / "eer_per_user.csv",
              ["username", "user_id", "metric", "enrollment_n", "EER", "n_genuine", "n_impostor"],
              per_user_rows)
    write_csv(out / "operating_points.csv",
              ["metric", "enrollment_n", "target_FAR", "k", "empirical_FAR", "FRR"],
              op_rows)

    print(f"Vyhodnotenie hotové. Výstupy: {out.resolve()}")
    print(f"Používatelia: {len(users)}, enrollment vzorky: {len(samples)}, auth pokusy: {len(attempts)}")


if __name__ == "__main__":
    main()
