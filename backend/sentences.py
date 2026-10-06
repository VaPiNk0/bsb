"""Vety pre challenge, po slovensky alebo po anglicky.

Poradie zdrojov:
1. LLM (OpenAI-kompatibilné API: Ollama, LM Studio, llama.cpp server, vLLM...), ak je nastavené
   ``LLM_URL``. Vety sa generujú po dávkach na pozadí a držia sa v zásobníku, aby prihlásenie
   nečakalo na model.
2. Zbierka hotových viet (``corpus.py``). Použije sa, keď LLM nie je nastavený, neodpovedá
   alebo je zásobník prázdny.

Každá veta prejde rovnakou validáciou: malé písmená a-z, medzery, bez diakritiky a interpunkcie.
"""
from __future__ import annotations

import json
import random
import threading
import unicodedata
import urllib.request
from collections import deque

from .config import settings
from .corpus import CORPUS
from .features import ALPHABET

LANGUAGES = ("sk", "en")
DEFAULT_LANGUAGE = "sk"

_rng = random.SystemRandom()  # nepredvídateľný výber: veta je súčasť challenge


def sanitize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = "".join(ch if ch in ALPHABET else " " for ch in text)
    return " ".join(text.split())


def is_valid(sentence: str) -> bool:
    words = sentence.split(" ")
    return (
        settings.PHRASE_MIN_LEN <= len(sentence) <= settings.PHRASE_MAX_LEN
        and all(ch in ALPHABET for ch in sentence)
        and len(words) >= 5
        and max(len(w) for w in words) <= 14
    )


# Príklady v prompte robia viac než pravidlá: malý model podľa nich trafí dĺžku aj tón.
PROMPTS = {
    "sk": (
        "Napis {n} roznych slovenskych viet o beznom zivote, ake by clovek naozaj povedal "
        "(praca, skola, jedlo, cestovanie, pocasie, rodina, technika). Kazda veta musi davat zmysel, "
        "byt gramaticky spravna a mat 6 az 9 slov, spolu {lo} az {hi} znakov. Pis bez diakritiky, "
        "len male pismena a medzery, bez interpunkcie a bez cisel. Jedna veta na riadok, bez cislovania "
        "a bez dalsieho textu.\n\nPriklady:\n{examples}"
    ),
    "en": (
        "Write {n} different plain english sentences about everyday life that a person would really say "
        "(work, school, food, travel, weather, family, technology). Each sentence must make sense and "
        "have 6 to 10 words, {lo} to {hi} characters in total. Use only lowercase letters and spaces, "
        "no punctuation, no digits, no apostrophes. One sentence per line, no numbering and no other "
        "text.\n\nExamples:\n{examples}"
    ),
}


def _call_llm(n: int, lang: str = DEFAULT_LANGUAGE) -> list[str]:
    prompt = PROMPTS[lang].format(
        n=n, lo=settings.PHRASE_MIN_LEN, hi=settings.PHRASE_MAX_LEN,
        examples="\n".join(_rng.sample(CORPUS[lang], 5)),
    )
    body = json.dumps({
        "model": settings.LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.9,
        "max_tokens": 600,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if settings.LLM_API_KEY:
        headers["Authorization"] = f"Bearer {settings.LLM_API_KEY}"
    request = urllib.request.Request(settings.LLM_URL.rstrip("/") + "/chat/completions", body, headers)
    with urllib.request.urlopen(request, timeout=settings.LLM_TIMEOUT) as response:  # noqa: S310
        payload = json.load(response)
    content = payload["choices"][0]["message"]["content"]
    return [s for s in (sanitize(line) for line in content.splitlines()) if is_valid(s)]


class SentenceSource:
    """Zdroj viet jedného jazyka."""

    def __init__(self, lang: str) -> None:
        self.lang = lang
        self._corpus = [s for s in CORPUS[lang] if is_valid(s)]
        self._pool: deque[str] = deque()
        # nedávno použité vety sa neopakujú; pri zbierke stačí polovica, inak by došli
        self._recent: deque[str] = deque(maxlen=len(self._corpus) // 2)
        self._lock = threading.Lock()
        self._refilling = False

    def _refill(self) -> None:
        try:
            batch = _call_llm(12, self.lang)
            with self._lock:
                for sentence in batch:
                    if sentence not in self._recent and sentence not in self._pool:
                        self._pool.append(sentence)
        except Exception:  # noqa: BLE001 - LLM je voliteľný, vždy máme zbierku
            pass
        finally:
            with self._lock:
                self._refilling = False

    def _from_corpus(self) -> str:
        fresh = [s for s in self._corpus if s not in self._recent]
        return _rng.choice(fresh or self._corpus)

    def next_phrase(self) -> tuple[str, str]:
        """Vráti (veta, zdroj) kde zdroj je 'llm' alebo 'corpus'."""
        start_refill = False
        with self._lock:
            if settings.LLM_URL and len(self._pool) < 6 and not self._refilling:
                self._refilling = start_refill = True
            if self._pool:
                sentence, source = self._pool.popleft(), "llm"
            else:
                sentence, source = self._from_corpus(), "corpus"
            self._recent.append(sentence)
        if start_refill:
            threading.Thread(target=self._refill, daemon=True).start()
        return sentence, source


sources = {lang: SentenceSource(lang) for lang in LANGUAGES}


def next_phrase(lang: str = DEFAULT_LANGUAGE) -> tuple[str, str]:
    return sources[lang if lang in sources else DEFAULT_LANGUAGE].next_phrase()
