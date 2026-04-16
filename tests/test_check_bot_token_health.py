import sys
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from scripts import check_bot_token_health as health


def _make_response(status_code: int, json_data: dict | None = None) -> MagicMock:
    response = MagicMock(spec=requests.Response)
    response.status_code = status_code
    response.ok = 200 <= status_code < 300
    response.json.return_value = json_data or {}
    return response


class TestCheckToken:
    def test_valid_token_prints_username(self, capsys):
        response = _make_response(200, {"username": "CoolBot", "id": "123"})
        with patch("scripts.check_bot_token_health.requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            mock_session.get.return_value = response

            result = health.check_token("valid-token", "DISCORD_BOT_TOKEN")

        assert result is True
        captured = capsys.readouterr()
        assert "Bot token OK: @CoolBot" in captured.out
        assert "DISCORD_BOT_TOKEN" in captured.out

    def test_401_response_triggers_health_monitor_warning(self, capsys, monkeypatch):
        posted_messages: list[str] = []

        def fake_post_warning(message: str) -> None:
            posted_messages.append(message)

        monkeypatch.setattr(health, "_post_health_monitor_warning", fake_post_warning)

        response = _make_response(401)
        with patch("scripts.check_bot_token_health.requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            mock_session.get.return_value = response

            result = health.check_token("bad-token", "DISCORD_BOT_TOKEN")

        assert result is False
        assert len(posted_messages) == 1
        assert "🔴" in posted_messages[0]
        assert "DISCORD_BOT_TOKEN" in posted_messages[0]
        assert "401" in posted_messages[0]
        captured = capsys.readouterr()
        assert "WARN" in captured.err

    def test_scheduling_token_valid_prints_username(self, capsys):
        response = _make_response(200, {"username": "ScheduleBot", "id": "456"})
        with patch("scripts.check_bot_token_health.requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            mock_session.get.return_value = response

            result = health.check_token("valid-sched-token", "DISCORD_SCHEDULING_BOT_TOKEN")

        assert result is True
        captured = capsys.readouterr()
        assert "Bot token OK: @ScheduleBot" in captured.out
        assert "DISCORD_SCHEDULING_BOT_TOKEN" in captured.out


class TestMissingTokenWarnings:
    def test_missing_bot_token_posts_health_monitor_warning(self, monkeypatch, capsys):
        posted_messages: list[str] = []

        def fake_post_warning(message: str) -> None:
            posted_messages.append(message)

        monkeypatch.setattr(health, "_post_health_monitor_warning", fake_post_warning)
        monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
        monkeypatch.delenv("DISCORD_SCHEDULING_BOT_TOKEN", raising=False)

        health.main()

        assert any("DISCORD_BOT_TOKEN" in m for m in posted_messages), "Expected warning for missing DISCORD_BOT_TOKEN"
        captured = capsys.readouterr()
        assert "WARN" in captured.err
        assert "DISCORD_BOT_TOKEN" in captured.err

    def test_missing_scheduling_token_posts_health_monitor_warning(self, monkeypatch, capsys):
        posted_messages: list[str] = []

        def fake_post_warning(message: str) -> None:
            posted_messages.append(message)

        monkeypatch.setattr(health, "_post_health_monitor_warning", fake_post_warning)
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "some-token")
        monkeypatch.delenv("DISCORD_SCHEDULING_BOT_TOKEN", raising=False)

        with patch("scripts.check_bot_token_health.requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            mock_session.get.return_value = _make_response(200, {"username": "Bot", "id": "1"})
            health.main()

        assert any("DISCORD_SCHEDULING_BOT_TOKEN" in m for m in posted_messages), \
            "Expected warning for missing DISCORD_SCHEDULING_BOT_TOKEN"
        captured = capsys.readouterr()
        assert "DISCORD_SCHEDULING_BOT_TOKEN" in captured.err


class TestInstagramSessionAge:
    def _make_session_file(self, tmp_path, age_days: float) -> str:
        path = tmp_path / "instaloader.session"
        path.write_text("session", encoding="utf-8")
        mtime = time.time() - age_days * 86400
        import os
        os.utime(str(path), (mtime, mtime))
        return str(path)

    def test_session_over_50_days_posts_health_monitor_warning(self, tmp_path, capsys, monkeypatch):
        posted_messages: list[str] = []

        def fake_post_warning(message: str) -> None:
            posted_messages.append(message)

        monkeypatch.setattr(health, "_post_health_monitor_warning", fake_post_warning)
        session_file = self._make_session_file(tmp_path, age_days=55)

        health.check_instagram_session_age(session_file)

        assert len(posted_messages) == 1
        assert "55" in posted_messages[0] or "day" in posted_messages[0]
        captured = capsys.readouterr()
        assert "WARN" in captured.err

    def test_session_between_30_and_50_days_prints_info_only(self, tmp_path, capsys, monkeypatch):
        posted_messages: list[str] = []

        def fake_post_warning(message: str) -> None:
            posted_messages.append(message)

        monkeypatch.setattr(health, "_post_health_monitor_warning", fake_post_warning)
        session_file = self._make_session_file(tmp_path, age_days=40)

        health.check_instagram_session_age(session_file)

        assert len(posted_messages) == 0
        captured = capsys.readouterr()
        assert "INFO" in captured.out
        assert "WARN" not in captured.err

    def test_session_under_30_days_no_output(self, tmp_path, capsys, monkeypatch):
        posted_messages: list[str] = []

        def fake_post_warning(message: str) -> None:
            posted_messages.append(message)

        monkeypatch.setattr(health, "_post_health_monitor_warning", fake_post_warning)
        session_file = self._make_session_file(tmp_path, age_days=10)

        health.check_instagram_session_age(session_file)

        assert len(posted_messages) == 0
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_missing_session_file_skips_gracefully(self, tmp_path, capsys, monkeypatch):
        posted_messages: list[str] = []

        def fake_post_warning(message: str) -> None:
            posted_messages.append(message)

        monkeypatch.setattr(health, "_post_health_monitor_warning", fake_post_warning)
        missing_path = str(tmp_path / "no_such_file.session")

        health.check_instagram_session_age(missing_path)

        assert len(posted_messages) == 0
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""
