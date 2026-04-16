import sys
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
