"""Logika frontendu, ktorá sa dá overiť bez prehliadača (spúšťa sa v Node)."""
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def test_form_cannot_be_submitted_twice_at_once():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node nie je nainštalovaný")
    result = subprocess.run([node, str(HERE / "frontend_submit_once.js")], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
