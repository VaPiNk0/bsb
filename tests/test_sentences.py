import io
import json

import pytest

from backend import sentences
from backend.corpus import CORPUS
from backend.sentences import LANGUAGES, is_valid, next_phrase, sanitize


@pytest.mark.parametrize("lang", LANGUAGES)
def test_corpus_sentences_are_valid_and_unique(lang):
    assert len(CORPUS[lang]) >= 100
    assert [s for s in CORPUS[lang] if not is_valid(s)] == []
    assert len(set(CORPUS[lang])) == len(CORPUS[lang])


@pytest.mark.parametrize("lang", LANGUAGES)
def test_phrases_come_from_the_requested_language_without_quick_repeats(lang):
    batch = [next_phrase(lang) for _ in range(40)]
    assert all(source == "corpus" and phrase in CORPUS[lang] for phrase, source in batch)
    assert len({phrase for phrase, _ in batch}) == 40


def test_unknown_language_falls_back_to_slovak():
    assert next_phrase("de")[0] in CORPUS["sk"]


def test_sanitize_strips_diacritics_and_punctuation():
    assert sanitize("Dnes večer čítam zaujímavú knihu!") == "dnes vecer citam zaujimavu knihu"


def test_llm_output_is_filtered(monkeypatch):
    content = chr(10).join(["1. Dnes večer čítam novú knihu v knižnici.", "krátka", "x" * 90])
    reply = {"choices": [{"message": {"content": content}}]}
    sent = []

    class FakeResponse(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(request, **kwargs):
        sent.append(json.loads(request.data)["messages"][0]["content"])
        return FakeResponse(json.dumps(reply).encode())

    monkeypatch.setattr(sentences.urllib.request, "urlopen", fake_urlopen)
    from types import SimpleNamespace
    fake = SimpleNamespace(**{k: getattr(sentences.settings, k) for k in dir(sentences.settings) if k.isupper()})
    fake.LLM_URL = "http://fake"
    monkeypatch.setattr(sentences, "settings", fake)
    result = sentences._call_llm(3, "en")
    assert result and all(is_valid(s) for s in result)
    assert "english" in sent[0] and "Examples:" in sent[0]      # prompt je v jazyku, ktorý sa pýta
