import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def fixture_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture_json(fixture_dir):
    def _load(name: str):
        return json.loads((fixture_dir / name).read_text(encoding="utf-8"))

    return _load
