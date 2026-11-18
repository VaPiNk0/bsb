"""Úložisko: lokálne SQLite (default), v nasadení Postgres (Supabase) cez DATABASE_URL.

SQL je spoločné pre obe databázy; líši sa len zástupný znak parametra, typy v schéme
a spôsob, akým INSERT vráti nové id.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import settings


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


IS_POSTGRES = bool(settings.DATABASE_URL)
TABLES = ["users", "templates", "enrollment_samples", "challenges", "auth_attempts", "refresh_samples", "template_history"]
# Na telefóne a na počítači píše človek inak (dotyková klávesnica nehlási ani držanie klávesy),
# preto má účet pre každý typ zariadenia vlastnú šablónu.
DEVICES = ("pc", "mobile")
_pool = None

if IS_POSTGRES:
    from psycopg import IntegrityError
else:
    from sqlite3 import IntegrityError


class _Postgres:
    """Rovnaké rozhranie ako sqlite3.Connection.execute, len so zástupným znakom %s."""

    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql: str, params: tuple | list = ()):
        return self.conn.execute(sql.replace("?", "%s"), params)


def _postgres_pool():
    global _pool
    if _pool is None:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        # Spojenie do Supabase stojí TLS handshake, preto sa drží v poole. prepare_threshold=None:
        # Supabase pooler v transakčnom režime nepodporuje pripravené príkazy.
        # check: pooler nečinné spojenie zavrie; bez overenia by prvá požiadavka po pauze spadla (500).
        _pool = ConnectionPool(
            settings.DATABASE_URL, min_size=1, max_size=5, open=True,
            check=ConnectionPool.check_connection,
            kwargs={"row_factory": dict_row, "prepare_threshold": None},
        )
    return _pool


@contextmanager
def _connect() -> Iterator[Any]:
    """Jedna transakcia: pri výnimke rollback, inak commit; spojenie sa vždy uvoľní."""
    if IS_POSTGRES:
        with _postgres_pool().connection() as conn:
            yield _Postgres(conn)
        return
    conn = sqlite3.connect(settings.DB_PATH, timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        with conn:
            yield conn
    finally:
        conn.close()


def _insert(conn, sql: str, params: tuple | list) -> int:
    if IS_POSTGRES:
        return int(conn.execute(sql + " RETURNING id", params).fetchone()["id"])
    return int(conn.execute(sql, params).lastrowid)


def _columns(conn, table: str) -> set[str]:
    if IS_POSTGRES:
        rows = conn.execute(
            "SELECT column_name AS name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = ?", (table,)
        ).fetchall()
    else:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def init_db() -> None:
    if not IS_POSTGRES:
        Path(settings.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        if not IS_POSTGRES:
            conn.execute("PRAGMA journal_mode=WAL")   # zapíše sa do súboru DB, stačí raz
        schema = (
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                template_json TEXT,
                failed_count INTEGER NOT NULL DEFAULT 0,
                lock_until TEXT,
                phrase_lang TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS templates (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                device TEXT NOT NULL,
                template_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                cycle INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, device)
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
                test_mode INTEGER,
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
        if IS_POSTGRES:
            schema = schema.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY")
            schema = schema.replace(" REAL", " DOUBLE PRECISION")   # REAL je v Postgrese len 4-bajtový
        for statement in schema.split(";"):
            if statement.strip():
                conn.execute(statement)
        # migrácia starších databáz: doplní stĺpec phrase (staré pevné-vetové vektory ale už nesedia)
        migrations = {
            "enrollment_samples": [("phrase", "TEXT"), ("cycle", "INTEGER NOT NULL DEFAULT 0"), ("device", "TEXT NOT NULL DEFAULT 'pc'")],
            "challenges": [("phrase", "TEXT"), ("device", "TEXT")],
            "auth_attempts": [("phrase", "TEXT"), ("test_mode", "INTEGER"), ("device", "TEXT")],
            "users": [("template_updated_at", "TEXT"), ("template_cycle", "INTEGER NOT NULL DEFAULT 0"), ("phrase_lang", "TEXT")],
            "refresh_samples": [("device", "TEXT NOT NULL DEFAULT 'pc'")],
            "template_history": [("device", "TEXT NOT NULL DEFAULT 'pc'")],
        }
        for table, wanted in migrations.items():
            columns = _columns(conn, table)
            for name, ddl in wanted:
                if name not in columns:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        # Šablóna bola kedysi priamo v users (jedna na účet, vznikla na počítači): presunie sa do templates.
        conn.execute(
            "INSERT INTO templates (user_id, device, template_json, updated_at, cycle) "
            "SELECT id, 'pc', template_json, COALESCE(template_updated_at, created_at), COALESCE(template_cycle, 0) "
            "FROM users WHERE template_json IS NOT NULL"
        )
        conn.execute("UPDATE users SET template_json = NULL WHERE template_json IS NOT NULL")
        if IS_POSTGRES:
            # Supabase vystavuje schému public cez REST API s verejným anon kľúčom. Zapnuté RLS
            # bez politík tam všetko zakáže; backend sa pripája ako vlastník tabuliek a RLS sa ho netýka.
            for table in TABLES:
                conn.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def feature_hash(features: list[float | None]) -> str:
    canonical = json.dumps([None if x is None else round(float(x), 3) for x in features], separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_user_by_username(username: str) -> Any | None:
    with _connect() as conn:
        return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def get_user(user_id: int) -> Any | None:
    with _connect() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def create_user(username: str, password_hash: str, phrase_lang: str | None = None) -> int:
    with _connect() as conn:
        return _insert(
            conn,
            "INSERT INTO users (username, password_hash, phrase_lang, created_at) VALUES (?, ?, ?, ?)",
            (username, password_hash, phrase_lang, now_iso()),
        )


def add_enrollment_sample(
    user_id: int, sample_index: int, features: list[float | None], phrase: str | None = None, cycle: int = 0,
    device: str = "pc",
) -> int:
    digest = feature_hash(features)
    with _connect() as conn:
        return _insert(
            conn,
            "INSERT INTO enrollment_samples "
            "(user_id, sample_index, features_json, feature_hash, phrase, cycle, device, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, sample_index, json.dumps(features), digest, phrase, cycle, device, now_iso()),
        )


def get_enrollment_samples(user_id: int, device: str | None = None) -> list[Any]:
    """Vzorky účtu, s `device` len z daného typu zariadenia."""
    with _connect() as conn:
        if device is None:
            return conn.execute(
                "SELECT * FROM enrollment_samples WHERE user_id = ? ORDER BY sample_index ASC", (user_id,)
            ).fetchall()
        return conn.execute(
            "SELECT * FROM enrollment_samples WHERE user_id = ? AND device = ? ORDER BY sample_index ASC",
            (user_id, device),
        ).fetchall()


def next_sample_index(user_id: int) -> int:
    """Poradie vzorky je spoločné pre všetky zariadenia účtu (tabuľka má UNIQUE(user_id, sample_index))."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(sample_index), 0) AS n FROM enrollment_samples WHERE user_id = ?", (user_id,)
        ).fetchone()
        return int(row["n"]) + 1


def get_template(user_id: int, device: str) -> Any | None:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM templates WHERE user_id = ? AND device = ?", (user_id, device)
        ).fetchone()


def get_templates(user_id: int) -> list[Any]:
    with _connect() as conn:
        return conn.execute("SELECT * FROM templates WHERE user_id = ? ORDER BY device", (user_id,)).fetchall()


def save_template(user_id: int, device: str, template: dict[str, Any]) -> None:
    """Uloží šablónu; nová dostane čas vzniku a cyklus 0, existujúca si ich ponechá."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO templates (user_id, device, template_json, updated_at, cycle) VALUES (?, ?, ?, ?, 0) "
            "ON CONFLICT (user_id, device) DO UPDATE SET template_json = excluded.template_json",
            (user_id, device, json.dumps(template, separators=(",", ":")), now_iso()),
        )


def create_challenge(
    user_id: int, purpose: str, token: str, ttl_seconds: int, phrase: str | None = None, device: str | None = None
) -> int:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    created = datetime.now(timezone.utc)
    expires = created + timedelta(seconds=ttl_seconds)
    with _connect() as conn:
        return _insert(
            conn,
            "INSERT INTO challenges (token_hash, user_id, purpose, phrase, device, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (digest, user_id, purpose, phrase, device, expires.isoformat(), created.isoformat()),
        )


def consume_challenge(token: str, user_id: int, purpose: str) -> Any | None:
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
        "replay_detected", "reason", "ip", "test_mode", "device", "created_at",
    ]
    values = [data.get(column) for column in columns]
    with _connect() as conn:
        return _insert(
            conn,
            f"INSERT INTO auth_attempts ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            values,
        )


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


def list_users() -> list[Any]:
    with _connect() as conn:
        return conn.execute(
            "SELECT id, username, failed_count, lock_until, created_at, "
            "(SELECT COUNT(*) FROM enrollment_samples e WHERE e.user_id = users.id) AS enrollment_count "
            "FROM users ORDER BY username"
        ).fetchall()


def list_attempts(limit: int = 100) -> list[Any]:
    with _connect() as conn:
        return conn.execute(
            "SELECT id, username, actor_label, attempt_type, impersonated_username, score, threshold, phrase, "
            "accepted, replay_detected, reason, ip, device, created_at FROM auth_attempts "
            "ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()


def list_report_attempts() -> list[Any]:
    """Všetky pokusy pre tabuľky výsledkov; bez IP, vety a vektora príznakov."""
    with _connect() as conn:
        return conn.execute(
            "SELECT id, created_at, user_id, username, actor_label, attempt_type, test_mode, score, threshold, "
            "accepted, replay_detected, reason, device FROM auth_attempts ORDER BY id ASC"
        ).fetchall()


def list_templates() -> list[Any]:
    with _connect() as conn:
        return conn.execute("SELECT * FROM templates ORDER BY user_id, device").fetchall()


def list_enrollment_features() -> list[Any]:
    with _connect() as conn:
        return conn.execute("SELECT user_id, device, features_json FROM enrollment_samples").fetchall()


# ---------- mesačná obnova šablóny ----------

def stamp_template(user_id: int, device: str, cycle: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE templates SET updated_at = ?, cycle = ? WHERE user_id = ? AND device = ?",
            (now_iso(), cycle, user_id, device),
        )


def age_template(user_id: int, days: int) -> None:
    """Len pre demo/test: posunie čas poslednej obnovy všetkých šablón účtu do minulosti."""
    for row in get_templates(user_id):
        with _connect() as conn:
            conn.execute(
                "UPDATE templates SET updated_at = ? WHERE user_id = ? AND device = ?",
                ((parse_iso(row["updated_at"]) - timedelta(days=days)).isoformat(), user_id, row["device"]),
            )


def add_refresh_sample(
    user_id: int, cycle: int, features: list[float | None], phrase: str, score: float, device: str = "pc"
) -> int:
    with _connect() as conn:
        return _insert(
            conn,
            "INSERT INTO refresh_samples (user_id, cycle, features_json, feature_hash, phrase, score, device, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, cycle, json.dumps(features), feature_hash(features), phrase, score, device, now_iso()),
        )


def get_refresh_samples(user_id: int, cycle: int, device: str = "pc") -> list[Any]:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM refresh_samples WHERE user_id = ? AND cycle = ? AND device = ? ORDER BY id ASC",
            (user_id, cycle, device),
        ).fetchall()


def clear_refresh_samples(user_id: int, device: str = "pc") -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM refresh_samples WHERE user_id = ? AND device = ?", (user_id, device))


def insert_history(data: dict[str, Any]) -> int:
    columns = [
        "user_id", "cycle", "verdict", "speed_change_pct", "consistency_change_pct", "shift_ratio",
        "median_dd_old", "median_dd_new", "old_threshold", "new_threshold", "threshold_capped",
        "new_samples", "device", "created_at",
    ]
    with _connect() as conn:
        return _insert(
            conn,
            f"INSERT INTO template_history ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            [data.get(c) for c in columns],
        )


def list_history(limit: int = 200) -> list[Any]:
    with _connect() as conn:
        return conn.execute(
            "SELECT h.*, u.username FROM template_history h JOIN users u ON u.id = h.user_id "
            "ORDER BY h.id ASC LIMIT ?",
            (int(limit),),
        ).fetchall()
