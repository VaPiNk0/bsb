from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    # Vety sa generujú pre každý pokus zvlášť (pozri sentences.py).
    PHRASE_MIN_LEN: int = int(os.getenv("PHRASE_MIN_LEN", "35"))
    PHRASE_MAX_LEN: int = int(os.getenv("PHRASE_MAX_LEN", "55"))
    MAX_CORRECTIONS: int = int(os.getenv("MAX_CORRECTIONS", "15"))
    # Voliteľný lokálny LLM (OpenAI-kompatibilné API), napr. Ollama: http://127.0.0.1:11434/v1
    LLM_URL: str = os.getenv("LLM_URL", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "qwen2.5:7b")
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_TIMEOUT: float = float(os.getenv("LLM_TIMEOUT", "30"))
    # Pri meniacom sa texte treba viac enrollment vzoriek než pri jednej pevnej vete.
    MIN_ENROLLMENT: int = int(os.getenv("MIN_ENROLLMENT", "12"))
    MAX_ENROLLMENT: int = int(os.getenv("MAX_ENROLLMENT", "20"))

    # Mesačná obnova šablóny (drift písania)
    TEMPLATE_REFRESH_DAYS: int = int(os.getenv("TEMPLATE_REFRESH_DAYS", "30"))
    REFRESH_SAMPLES: int = int(os.getenv("REFRESH_SAMPLES", "6"))
    REFRESH_MAX_RATIO: float = float(os.getenv("REFRESH_MAX_RATIO", "1.6"))      # vzorka nad 1.6x prahu sa odmietne
    THRESHOLD_MAX_GROWTH: float = float(os.getenv("THRESHOLD_MAX_GROWTH", "0.25"))  # prah môže za cyklus vyrásť max o 25 %
    THRESHOLD_MAX_SHRINK: float = float(os.getenv("THRESHOLD_MAX_SHRINK", "0.30"))  # a klesnúť max o 30 %

    # Manhattan je primárny; Mahalanobis sa dá prepnúť cez METRIC.
    METRIC: str = os.getenv("METRIC", "manhattan").lower()
    K: float = float(os.getenv("K", "2.0"))
    MAD_FLOOR_MS: float = float(os.getenv("MAD_FLOOR_MS", "5.0"))
    ROBUST_Z_LIMIT: float = float(os.getenv("ROBUST_Z_LIMIT", "3.5"))
    COV_SHRINKAGE: float = float(os.getenv("COV_SHRINKAGE", "0.15"))

    # Bezpečnostné limity pre demo server.
    LOCKOUT_THRESHOLD: int = int(os.getenv("LOCKOUT_THRESHOLD", "5"))
    LOCKOUT_SECONDS: int = int(os.getenv("LOCKOUT_SECONDS", "300"))
    RATE_LIMIT_WINDOW_SECONDS: int = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
    RATE_LIMIT_MAX_ATTEMPTS: int = int(os.getenv("RATE_LIMIT_MAX_ATTEMPTS", "15"))
    CHALLENGE_TTL_SECONDS: int = int(os.getenv("CHALLENGE_TTL_SECONDS", "90"))

    # Test mode je určený LEN na zber dát (zapína sa premennou prostredia, defaultne vypnutý:
    # klient si ho inak vie vypýtať sám a obišiel by lockout). Lockout sa v ňom defaultne neuplatní,
    # aby jeden experiment nezablokoval merané účty.
    ALLOW_TEST_MODE: bool = os.getenv("ALLOW_TEST_MODE", "0") == "1"
    ENFORCE_LOCKOUT_IN_TEST_MODE: bool = os.getenv("ENFORCE_LOCKOUT_IN_TEST_MODE", "0") == "1"

    # Voliteľná adaptácia po úspešnom genuine prihlásení.
    ADAPTIVE_TEMPLATE: bool = os.getenv("ADAPTIVE_TEMPLATE", "0") == "1"
    ADAPTIVE_ALPHA: float = float(os.getenv("ADAPTIVE_ALPHA", "0.05"))

    SECRET_KEY: str = os.getenv("SECRET_KEY") or secrets.token_hex(32)
    DB_PATH: str = os.getenv("DB_PATH", str(PROJECT_ROOT / "data" / "keystroke.db"))

    def validate(self) -> None:
        if not (0.0 <= self.K <= 10.0):
            raise ValueError("K musí byť v rozsahu 0..10")
        if self.METRIC not in {"manhattan", "mahalanobis"}:
            raise ValueError("METRIC musí byť 'manhattan' alebo 'mahalanobis'")
        if not (0.0 < self.COV_SHRINKAGE <= 1.0):
            raise ValueError("COV_SHRINKAGE musí byť v rozsahu (0,1]")
        if self.MIN_ENROLLMENT < 5 or self.MAX_ENROLLMENT < self.MIN_ENROLLMENT:
            raise ValueError("Neplatný rozsah počtu enrollment vzoriek")
        if self.TEMPLATE_REFRESH_DAYS < 1 or self.REFRESH_SAMPLES < 3:
            raise ValueError("Neplatné nastavenie mesačnej obnovy")
        if not (20 <= self.PHRASE_MIN_LEN < self.PHRASE_MAX_LEN <= 80):
            raise ValueError("Neplatný rozsah dĺžky vety")


settings = Settings()
settings.validate()
