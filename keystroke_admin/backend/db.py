from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import settings


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _connect() -> sqlite3.Connection:
    Path(settings.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                template_json TEXT,
                failed_count INTEGER NOT NULL DEFAULT 0,
                lock_until TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS enrollment_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                sample_index INTEGER NOT NULL,
                features_json TEXT NOT NULL,
                feature_hash TEXT NOT NULL,
                phrase TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, sample_index),
                UNIQUE(user_id, feature_hash)
            );

            CREATE TABLE IF NOT EXISTS challenges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                purpose TEXT NOT NULL,
                phrase TEXT,
                expires_at TEXT NOT NULL,
                used_at TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS auth_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                username TEXT NOT NULL,
                actor_label TEXT,
                attempt_type TEXT NOT NULL,
                impersonated_username TEXT,
                score REAL,
                threshold REAL,
                accepted INTEGER,
                feature_json TEXT,
                feature_hash TEXT,
                phrase TEXT,
                challenge_id INTEGER,
                replay_detected INTEGER NOT NULL DEFAULT 0,
                reason TEXT,
                ip TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS refresh_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                cycle INTEGER NOT NULL,
                features_json TEXT NOT NULL,
                feature_hash TEXT NOT NULL,
                phrase TEXT,
                score REAL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS template_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                cycle INTEGER NOT NULL,
                verdict TEXT NOT NULL,
                speed_change_pct REAL,
                consistency_change_pct REAL,
                shift_ratio REAL,
                median_dd_old REAL,
                median_dd_new REAL,
                old_threshold REAL,
                new_threshold REAL,
                threshold_capped INTEGER NOT NULL DEFAULT 0,
                new_samples INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_attempts_user_time
                ON auth_attempts(user_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_attempts_ip_time
                ON auth_attempts(ip, created_at);
            CREATE INDEX IF NOT EXISTS idx_attempts_feature_hash
                ON auth_attempts(user_id, feature_hash);
            """
        )
        # migrácia starších databáz: doplní stĺpec phrase (staré pevné-vetové vektory ale už nesedia)
        migrations = {
            "enrollment_samples": [("phrase", "TEXT"), ("cycle", "INTEGER NOT NULL DEFAULT 0")],
            "challenges": [("phrase", "TEXT")],
            "auth_attempts": [("phrase", "TEXT")],
            "users": [("template_updated_at", "TEXT"), ("template_cycle", "INTEGER NOT NULL DEFAULT 0")],
        }
        for table, wanted in migrations.items():
            columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            for name, ddl in wanted:
                if name not in columns:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def feature_hash(features: list[float | None]) -> str:
    canonical = json.dumps([None if x is None else round(float(x), 3) for x in features], separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_user_by_username(username: str) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def get_user(user_id: int) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def create_user(username: str, password_hash: str) -> int:
    with _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, password_hash, now_iso()),
        )
        return int(cursor.lastrowid)


def add_enrollment_sample(
    user_id: int, sample_index: int, features: list[float | None], phrase: str | None = None, cycle: int = 0
) -> int:
    digest = feature_hash(features)
    with _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO enrollment_samples "
            "(user_id, sample_index, features_json, feature_hash, phrase, cycle, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, sample_index, json.dumps(features), digest, phrase, cycle, now_iso()),
        )
        return int(cursor.lastrowid)


def get_enrollment_samples(user_id: int) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM enrollment_samples WHERE user_id = ? ORDER BY sample_index ASC", (user_id,)
        ).fetchall()


def save_template(user_id: int, template: dict[str, Any]) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET template_json = ? WHERE id = ?",
            (json.dumps(template, separators=(",", ":")), user_id),
        )


def create_challenge(user_id: int, purpose: str, token: str, ttl_seconds: int, phrase: str | None = None) -> int:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    created = datetime.now(timezone.utc)
    expires = created + timedelta(seconds=ttl_seconds)
    with _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO challenges (token_hash, user_id, purpose, phrase, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (digest, user_id, purpose, phrase, expires.isoformat(), created.isoformat()),
        )
        return int(cursor.lastrowid)


def consume_challenge(token: str, user_id: int, purpose: str) -> sqlite3.Row | None:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM challenges WHERE token_hash = ? AND user_id = ? AND purpose = ?",
            (digest, user_id, purpose),
        ).fetchone()
        if not row or row["used_at"]:
            return None
        if parse_iso(row["expires_at"]) < datetime.now(timezone.utc):
            return None
        conn.execute("UPDATE challenges SET used_at = ? WHERE id = ?", (now_iso(), row["id"]))
        return row


def recent_attempt_count(ip: str, username: str, window_seconds: int) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM auth_attempts "
            "WHERE ip = ? AND username = ? AND created_at >= ?",
            (ip, username, cutoff.isoformat()),
        ).fetchone()
        return int(row["n"])


def duplicate_attempt_exists(user_id: int, digest: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM auth_attempts WHERE user_id = ? AND feature_hash = ? LIMIT 1",
            (user_id, digest),
        ).fetchone()
        return row is not None


def insert_attempt(data: dict[str, Any]) -> int:
    columns = [
        "user_id", "username", "actor_label", "attempt_type", "impersonated_username",
        "score", "threshold", "accepted", "feature_json", "feature_hash", "phrase", "challenge_id",
        "replay_detected", "reason", "ip", "created_at",
    ]
    values = [data.get(column) for column in columns]
    with _connect() as conn:
        cursor = conn.execute(
            f"INSERT INTO auth_attempts ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            values,
        )
        return int(cursor.lastrowid)


def update_auth_state(user_id: int, accepted: bool, lock_threshold: int, lock_seconds: int) -> None:
    with _connect() as conn:
        user = conn.execute("SELECT failed_count FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return
        if accepted:
            conn.execute("UPDATE users SET failed_count = 0, lock_until = NULL WHERE id = ?", (user_id,))
            return
        failures = int(user["failed_count"]) + 1
        if failures >= lock_threshold:
            lock_until = (datetime.now(timezone.utc) + timedelta(seconds=lock_seconds)).isoformat()
            conn.execute(
                "UPDATE users SET failed_count = ?, lock_until = ? WHERE id = ?",
                (failures, lock_until, user_id),
            )
        else:
            conn.execute("UPDATE users SET failed_count = ? WHERE id = ?", (failures, user_id))


def list_users() -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT id, username, failed_count, lock_until, created_at, template_updated_at, template_cycle, "
            "(SELECT COUNT(*) FROM enrollment_samples e WHERE e.user_id = users.id) AS enrollment_count, "
            "CASE WHEN template_json IS NULL THEN 0 ELSE 1 END AS enrolled "
            "FROM users ORDER BY username"
        ).fetchall()


def list_attempts(limit: int = 100) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT id, username, actor_label, attempt_type, impersonated_username, score, threshold, phrase, "
            "accepted, replay_detected, reason, ip, created_at FROM auth_attempts "
            "ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()


# ---------- mesačná obnova šablóny ----------

def stamp_template(user_id: int, cycle: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET template_updated_at = ?, template_cycle = ? WHERE id = ?",
            (now_iso(), cycle, user_id),
        )


def age_template(user_id: int, days: int) -> None:
    """Len pre demo/test: posunie čas poslednej obnovy do minulosti."""
    user = get_user(user_id)
    if not user:
        return
    base = parse_iso(user["template_updated_at"] or user["created_at"])
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET template_updated_at = ? WHERE id = ?",
            ((base - timedelta(days=days)).isoformat(), user_id),
        )


def add_refresh_sample(user_id: int, cycle: int, features: list[float | None], phrase: str, score: float) -> int:
    with _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO refresh_samples (user_id, cycle, features_json, feature_hash, phrase, score, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, cycle, json.dumps(features), feature_hash(features), phrase, score, now_iso()),
        )
        return int(cursor.lastrowid)


def get_refresh_samples(user_id: int, cycle: int) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM refresh_samples WHERE user_id = ? AND cycle = ? ORDER BY id ASC", (user_id, cycle)
        ).fetchall()


def clear_refresh_samples(user_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM refresh_samples WHERE user_id = ?", (user_id,))


def insert_history(data: dict[str, Any]) -> int:
    columns = [
        "user_id", "cycle", "verdict", "speed_change_pct", "consistency_change_pct", "shift_ratio",
        "median_dd_old", "median_dd_new", "old_threshold", "new_threshold", "threshold_capped",
        "new_samples", "created_at",
    ]
    with _connect() as conn:
        cursor = conn.execute(
            f"INSERT INTO template_history ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            [data.get(c) for c in columns],
        )
        return int(cursor.lastrowid)


def list_history(limit: int = 200) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT h.*, u.username FROM template_history h JOIN users u ON u.id = h.user_id "
            "ORDER BY h.id ASC LIMIT ?",
            (int(limit),),
        ).fetchall()
