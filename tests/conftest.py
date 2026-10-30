from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def corpus_release_1() -> Path:
    return ROOT / "data" / "corpus" / "release_1"
