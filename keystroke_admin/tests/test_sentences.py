import io
import json

from backend import sentences
from backend.sentences import is_valid, sanitize, template_sentence


def test_template_sentences_are_valid_and_varied():
    batch = {template_sentence() for _ in range(60)}
    assert all(is_valid(s) for s in batch)
    assert len(batch) > 30


def test_sanitize_strips_diacritics_and_punctuation():
    assert sanitize("Dnes večer čítam zaujímavú knihu!") == "dnes vecer citam zaujimavu knihu"


def test_llm_output_is_filtered(monkeypatch):
    reply = {"choices": [{"message": {"content": "1. Dnes večer čítam novú knihu v knižnici.\nkrátka\n" + "x" * 90}}]}

    class FakeResponse(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(sentences.urllib.request, "urlopen", lambda *a, **k: FakeResponse(json.dumps(reply).encode()))
    from types import SimpleNamespace
    fake = SimpleNamespace(**{**vars(sentences.settings.__class__), **{k: getattr(sentences.settings, k) for k in dir(sentences.settings) if k.isupper()}})
    fake.LLM_URL = "http://fake"
    monkeypatch.setattr(sentences, "settings", fake)
    result = sentences._call_llm(3)
    assert result and all(is_valid(s) for s in result)
