"""Small Discord API helper utilities with retry and recovery signals."""

import time
from typing import Any

import requests

DISCORD_API_BASE = "https://discord.com/api/v10"
DEFAULT_TIMEOUT_SECONDS = 30
RETRY_STATUSES = {429, 500, 502, 503, 504}


class DiscordApiError(RuntimeError):
    def __init__(self, message: str, response: requests.Response | None = None):
        super().__init__(message)
        self.response = response


class DiscordMessageNotFoundError(DiscordApiError):
    pass


class DiscordClient:
    def __init__(self, session: requests.Session, *, timeout: int = DEFAULT_TIMEOUT_SECONDS, max_retries: int = 5):
        self.session = session
        self.timeout = timeout
        self.max_retries = max_retries

    def request(self, method: str, url: str, *, context: str, json_payload: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> requests.Response:
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.request(method=method, url=url, json=json_payload, params=params, timeout=self.timeout)
            except requests.RequestException as error:
                last_error = error
                if attempt == self.max_retries:
                    raise DiscordApiError(f"{context} failed after retries: {error}") from error
                sleep_seconds = float(attempt)
                print(f"DISCORD RETRY: {context}; attempt={attempt}/{self.max_retries}; request exception={error}; sleeping {sleep_seconds:.1f}s")
                time.sleep(sleep_seconds)
                continue

            if response.status_code in RETRY_STATUSES:
                if attempt == self.max_retries:
                    self._raise_for_status(response, context)
                sleep_seconds = self._get_retry_after_seconds(response, attempt)
                print(f"DISCORD RETRY: {context}; attempt={attempt}/{self.max_retries}; status={response.status_code}; sleeping {sleep_seconds:.1f}s")
                time.sleep(sleep_seconds)
                continue

            if response.status_code == 404 and self._is_unknown_message(response):
                raise DiscordMessageNotFoundError(f"{context}: Discord message not found", response=response)

            self._raise_for_status(response, context)
            return response

        raise DiscordApiError(f"{context} exhausted retries unexpectedly: {last_error}")

    def get_message(self, channel_id: str, message_id: str, *, context: str) -> dict[str, Any]:
        response = self.request("GET", f"{DISCORD_API_BASE}/channels/{channel_id}/messages/{message_id}", context=context)
        return self._parse_json_object(response, f"{context} JSON")

    def get_current_user(self, *, context: str) -> dict[str, Any]:
        response = self.request("GET", f"{DISCORD_API_BASE}/users/@me", context=context)
        return self._parse_json_object(response, f"{context} JSON")

    def get_reaction_users(
        self,
        channel_id: str,
        message_id: str,
        encoded_emoji: str,
        *,
        context: str,
        limit: int = 100,
        after: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if after:
            params["after"] = after
        response = self.request(
            "GET",
            f"{DISCORD_API_BASE}/channels/{channel_id}/messages/{message_id}/reactions/{encoded_emoji}",
            context=context,
            params=params,
        )
        return self._parse_json_array(response, f"{context} JSON")

    def post_message(self, channel_id: str, content: str, *, context: str) -> dict[str, Any]:
        response = self.request("POST", f"{DISCORD_API_BASE}/channels/{channel_id}/messages", context=context, json_payload={"content": content})
        return self._parse_json_object(response, f"{context} JSON")

    def edit_message(self, channel_id: str, message_id: str, content: str, *, context: str) -> dict[str, Any]:
        response = self.request("PATCH", f"{DISCORD_API_BASE}/channels/{channel_id}/messages/{message_id}", context=context, json_payload={"content": content})
        return self._parse_json_object(response, f"{context} JSON")

    def put_reaction(self, channel_id: str, message_id: str, encoded_emoji: str, *, context: str) -> None:
        self.request("PUT", f"{DISCORD_API_BASE}/channels/{channel_id}/messages/{message_id}/reactions/{encoded_emoji}/@me", context=context)

    @staticmethod
    def _parse_json_object(response: requests.Response, context: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as error:
            raise DiscordApiError(f"{context} was not valid JSON") from error
        if not isinstance(payload, dict):
            raise DiscordApiError(f"{context} did not return an object")
        return payload

    @staticmethod
    def _parse_json_array(response: requests.Response, context: str) -> list[dict[str, Any]]:
        try:
            payload = response.json()
        except ValueError as error:
            raise DiscordApiError(f"{context} was not valid JSON") from error
        if not isinstance(payload, list):
            raise DiscordApiError(f"{context} did not return an array")
        parsed: list[dict[str, Any]] = []
        for item in payload:
            if isinstance(item, dict):
                parsed.append(item)
        return parsed

    @staticmethod
    def _get_retry_after_seconds(response: requests.Response, attempt: int) -> float:
        header_value = response.headers.get("Retry-After")
        if header_value is not None:
            try:
                return max(float(header_value), 0.0)
            except ValueError:
                pass

        try:
            payload = response.json()
            retry_after = payload.get("retry_after")
            if isinstance(retry_after, (int, float)):
                return max(float(retry_after), 0.0)
        except ValueError:
            pass

        return float(attempt)

    @staticmethod
    def _is_unknown_message(response: requests.Response) -> bool:
        try:
            payload = response.json()
        except ValueError:
            return False
        return isinstance(payload, dict) and int(payload.get("code", 0)) == 10008

    @staticmethod
    def _raise_for_status(response: requests.Response, context: str) -> None:
        if response.ok:
            return
        body = response.text[:500]
        raise DiscordApiError(f"{context} failed: HTTP {response.status_code}; body={body}", response=response)
