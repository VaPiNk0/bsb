"""Generátor viet pre challenge.

Poradie zdrojov:
1. LLM (OpenAI-kompatibilné API: Ollama, LM Studio, llama.cpp server, vLLM...), ak je nastavené
   ``LLM_URL``. Viety sa generujú po dávkach na pozadí a držia sa v zásobníku, aby prihlásenie
   nečakalo na model.
2. Offline šablónový generátor (bez závislostí). Použije sa, keď LLM nie je nastavený,
   neodpovedá alebo je zásobník prázdny.

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
from .features import ALPHABET

_rng = random.SystemRandom()  # nepredvídateľný výber: veta je súčasť challenge

TIME = [
    "dnes rano", "dnes vecer", "zajtra poobede", "v piatok", "v pondelok rano", "cez vikend",
    "kazdy den", "vcera neskoro", "po obede", "pred polnocou", "v stredu", "v nedelu",
]
SUBJ = [
    "moj brat", "nasa ucitelka", "stary kolega", "mlada lekarka", "sused z prveho poschodia",
    "kamaratka z mesta", "novy sef", "cela rodina", "stara mama", "mily predavac", "nas technik",
    "hlavny inzinier", "mala sestra", "znamy fotograf",
]
VERB_OBJ = [
    "cita zaujimavu knihu", "pise dlhy list", "pripravuje vecheru", "opravuje stary pocitac",
    "hlada novu pracu", "kupuje cerstvy chlieb", "pozera dobry film", "planuje dlhu cestu",
    "vari hustu polievku", "meria nove kable", "navstevuje vzdialene mesto", "testuje novu siet",
    "pocuva tichu hudbu", "kresli farebny obrazok", "skusa nove recepty", "triedi stare fotky",
]
PLACE = [
    "v malej kaviarni", "pri velkom okne", "doma v kuchyni", "v tichej kniznici",
    "na hlavnej stanici", "pred starou skolou", "v nasej kancelarii", "pri rieke",
    "na rovnom namesti", "v prijemnej zahrade",
]
TEMPLATES = [
    "{time} {subj} {verb}",
    "{subj} {verb} {place}",
    "{time} {subj} {verb} {place}",
    "{subj} {time} {verb}",
]


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


def template_sentence() -> str:
    for _ in range(300):
        sentence = _rng.choice(TEMPLATES).format(
            time=_rng.choice(TIME), subj=_rng.choice(SUBJ),
            verb=_rng.choice(VERB_OBJ), place=_rng.choice(PLACE),
        )
        if is_valid(sentence):
            return sentence
    return "dnes vecer cita moj brat novu knihu"  # poistka, prakticky nikdy


PROMPT = (
    "Vymysli {n} rozdielnych kratkych viet po slovensky na vsedne temy (praca, skola, jedlo, "
    "cestovanie, pocasie, technika). Kazda veta ma mat {lo} az {hi} znakov. Jedna veta na riadok, "
    "bez cislovania, bez interpunkcie, bez cisel, vsetko malymi pismenami."
)


def _call_llm(n: int) -> list[str]:
    body = json.dumps({
        "model": settings.LLM_MODEL,
        "messages": [{"role": "user", "content": PROMPT.format(
            n=n, lo=settings.PHRASE_MIN_LEN, hi=settings.PHRASE_MAX_LEN)}],
        "temperature": 1.0,
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
    def __init__(self) -> None:
        self._pool: deque[str] = deque()
        self._recent: deque[str] = deque(maxlen=200)
        self._lock = threading.Lock()
        self._refilling = False

    def _refill(self) -> None:
        try:
            batch = _call_llm(12)
            with self._lock:
                for sentence in batch:
                    if sentence not in self._recent and sentence not in self._pool:
                        self._pool.append(sentence)
        except Exception:  # noqa: BLE001 - LLM je voliteľný, vždy máme fallback
            pass
        finally:
            with self._lock:
                self._refilling = False

    def next_phrase(self) -> tuple[str, str]:
        """Vráti (veta, zdroj) kde zdroj je 'llm' alebo 'template'."""
        start_refill = False
        with self._lock:
            if settings.LLM_URL and len(self._pool) < 6 and not self._refilling:
                self._refilling = start_refill = True
            if self._pool:
                sentence, source = self._pool.popleft(), "llm"
            else:
                sentence, source = template_sentence(), "template"
            self._recent.append(sentence)
        if start_refill:
            threading.Thread(target=self._refill, daemon=True).start()
        return sentence, source


source = SentenceSource()


def next_phrase() -> tuple[str, str]:
    return source.next_phrase()
