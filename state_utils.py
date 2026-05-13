"""Shared JSON state/retention helpers with atomic writes."""

import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Callable

try:
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
except Exception:
    _ET = None  # type: ignore[assignment]


LogFn = Callable[[str], None]


def _default_log(message: str) -> None:
    print(message)


def format_et_timestamp(value: Any) -> str | None:
    """Format an ISO UTC timestamp string as 'Dec 15, 2024 at 7:00 AM ET'.

    Returns None if the value is not a valid timestamp string.
    Falls back to UTC label if the Eastern timezone is unavailable.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if _ET is None:
        return parsed.astimezone(timezone.utc).strftime("%b %-d, %Y at %-I:%M %p UTC")
    et = parsed.astimezone(_ET)
    hour_12 = (et.hour % 12) or 12
    return f"{et.strftime('%b')} {et.day}, {et.year} at {hour_12}:{et.minute:02d} {et.strftime('%p')} ET"


def ensure_parent_dir(path: str) -> None:
    parent_dir = os.path.dirname(path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)


def load_json_object(path: str, *, default: dict[str, Any] | None = None, log: LogFn | None = None) -> dict[str, Any]:
    logger = log or _default_log
    fallback = {} if default is None else default

    if not os.path.exists(path):
        return dict(fallback)

    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError as error:
        logger(f"STATE WARN: invalid JSON in {path}; using default object ({error})")
        return dict(fallback)
    except OSError as error:
        logger(f"STATE WARN: failed to read {path}; using default object ({error})")
        return dict(fallback)

    if not isinstance(data, dict):
        logger(f"STATE WARN: expected JSON object in {path}; using default object")
        return dict(fallback)

    return data


def save_json_object_atomic(path: str, data: dict[str, Any]) -> None:
    ensure_parent_dir(path)
    directory = os.path.dirname(path) or "."

    fd, temp_path = tempfile.mkstemp(prefix=".tmp-state-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
            json.dump(data, temp_file, indent=2, ensure_ascii=False)
            temp_file.write("\n")
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def prune_latest_keys(data: dict[str, Any], keep_last: int) -> dict[str, Any]:
    if len(data) <= keep_last:
        return data
    keys_to_keep = set(sorted(data.keys())[-keep_last:])
    return {key: value for key, value in data.items() if key in keys_to_keep}


def prune_latest_iso_dates(data: dict[str, Any], keep_last: int, *, log: LogFn | None = None) -> dict[str, Any]:
    if len(data) <= keep_last:
        return data

    logger = log or _default_log

    def sort_key(key: str) -> tuple[int, datetime | str]:
        try:
            return (1, datetime.fromisoformat(key))
        except ValueError:
            logger(f"STATE WARN: non-ISO date key encountered during prune: {key}")
            return (0, key)

    kept_keys = {key for key, _ in sorted(data.items(), key=lambda item: sort_key(item[0]))[-keep_last:]}
    return {key: value for key, value in data.items() if key in kept_keys}
