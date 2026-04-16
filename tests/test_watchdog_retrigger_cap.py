"""Structural tests for the watchdog re-trigger cap state file.

The cap logic itself lives in JavaScript inside watchdog.yml and cannot be
unit-tested directly. These tests validate the persisted state file structure
so regressions in file format are caught before they reach CI.
"""

import json
import re
from pathlib import Path

COUNTS_FILE = Path(__file__).resolve().parent.parent / "data" / "watchdog_retrigger_counts.json"


def test_retrigger_counts_file_exists():
    """data/watchdog_retrigger_counts.json must exist and be valid JSON."""
    assert COUNTS_FILE.exists(), f"Expected {COUNTS_FILE} to exist"
    content = COUNTS_FILE.read_text(encoding="utf-8")
    # Must parse without error
    json.loads(content)


def test_retrigger_counts_initial_state():
    """The file must load as a dict (empty or populated)."""
    data = json.loads(COUNTS_FILE.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"Expected dict, got {type(data).__name__}"


def test_retrigger_counts_structure():
    """If the file has entries, keys must follow 'filename.yml:YYYY-MM-DD' format."""
    data = json.loads(COUNTS_FILE.read_text(encoding="utf-8"))
    key_pattern = re.compile(r"^[\w\-]+\.yml:\d{4}-\d{2}-\d{2}$")
    for key, value in data.items():
        assert key_pattern.match(key), (
            f"Key {key!r} does not match expected pattern 'filename.yml:YYYY-MM-DD'"
        )
        assert isinstance(value, int) and value >= 0, (
            f"Value for {key!r} must be a non-negative int, got {value!r}"
        )
