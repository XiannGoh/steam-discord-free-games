"""Small Discord API helper utilities with retry and recovery signals."""

import time
from typing import Any

import requests

DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_MESSAGE_HARD_LIMIT = 2000
DISCORD_MESSAGE_TARGET_LIMIT = 1900
DEFAULT_TIMEOUT_SECONDS = 30
RETRY_STATUSES = {429, 500, 502, 503, 504}

# Discord permission bitflags
PERM_ADMINISTRATOR = 1 << 3
PERM_ADD_REACTIONS = 1 << 6
PERM_VIEW_CHANNEL = 1 << 10
PERM_SEND_MESSAGES = 1 << 11
PERM_MANAGE_MESSAGES = 1 << 13
PERM_READ_MESSAGE_HISTORY = 1 << 16


def split_discord_content(
    content: str,
    *,
    target_limit: int = DISCORD_MESSAGE_TARGET_LIMIT,
    hard_limit: int = DISCORD_MESSAGE_HARD_LIMIT,
) -> list[str]:
    """Split long content into Discord-safe chunks with readable boundaries."""
    text = str(content)
    if len(text) <= hard_limit:
        return [text]

    if target_limit <= 0 or hard_limit <= 0:
        raise ValueError("Discord chunk limits must be positive")
    if target_limit > hard_limit:
        target_limit = hard_limit

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= hard_limit:
            chunks.append(remaining)
            break

        window = remaining[:target_limit]
        split_at = _best_split_index(window)
        if split_at <= 0:
            split_at = target_limit

        chunk = remaining[:split_at].rstrip("\n")
        if not chunk:
            chunk = remaining[:target_limit]
            split_at = len(chunk)

        if len(chunk) > hard_limit:
            chunk = chunk[:hard_limit]
            split_at = len(chunk)

        chunks.append(chunk)
        remaining = remaining[split_at:].lstrip("\n")

    for chunk in chunks:
        if len(chunk) > hard_limit:
            raise ValueError("Generated Discord chunk exceeded hard limit")

    return chunks


def _best_split_index(window: str) -> int:
    for token in ("\n\n", "\n- ", "\n* ", "\n• ", "\n"):
        idx = window.rfind(token)
        if idx > 0:
            return idx + len(token)
    return window.rfind(" ")


class DiscordApiError(RuntimeError):
    def __init__(self, message: str, response: requests.Response | None = None):
        super().__init__(message)
        self.response = response


class DiscordMessageNotFoundError(DiscordApiError):
    pass


class DiscordPermissionError(DiscordApiError):
    def __init__(
        self,
        message: str,
        *,
        channel_id: str = "",
        permission: str = "",
        response: requests.Response | None = None,
    ):
        super().__init__(message, response=response)
        self.channel_id = channel_id
        self.permission = permission


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

            if response.status_code == 403:
                raise DiscordPermissionError(
                    f"{context}: Discord returned 403 Forbidden — bot is missing permissions",
                    response=response,
                )

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

    def get_channel_messages(
        self,
        channel_id: str,
        *,
        context: str,
        limit: int = 100,
        before: str | None = None,
        after: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if before:
            params["before"] = before
        if after:
            params["after"] = after
        response = self.request("GET", f"{DISCORD_API_BASE}/channels/{channel_id}/messages", context=context, params=params)
        return self._parse_json_array(response, f"{context} JSON")

    def check_bot_permissions(self, channel_id: str, guild_id: str, *, bot_user_id: str = "") -> int:
        """Return the bot's effective permission bitfield for the given channel.

        Fetches guild member roles, role permissions, and channel permission overwrites,
        then computes the effective bitfield.  Returns 0 on any API error (best-effort
        check — the caller should treat 0 as "permissions unknown").
        """
        try:
            member_resp = self.request(
                "GET",
                f"{DISCORD_API_BASE}/guilds/{guild_id}/members/@me",
                context="check bot guild member",
            )
            member = self._parse_json_object(member_resp, "guild member JSON")
            bot_role_ids: set[str] = {str(r) for r in member.get("roles", [])}

            roles_resp = self.request(
                "GET",
                f"{DISCORD_API_BASE}/guilds/{guild_id}/roles",
                context="check guild roles",
            )
            roles = self._parse_json_array(roles_resp, "guild roles JSON")

            base_permissions = 0
            for role in roles:
                role_id = str(role.get("id", ""))
                perms = int(role.get("permissions", 0))
                if role_id == str(guild_id) or role_id in bot_role_ids:
                    base_permissions |= perms

            if base_permissions & PERM_ADMINISTRATOR:
                return (1 << 53) - 1  # all permissions

            channel_resp = self.request(
                "GET",
                f"{DISCORD_API_BASE}/channels/{channel_id}",
                context="check channel overwrites",
            )
            channel = self._parse_json_object(channel_resp, "channel JSON")
            overwrites: list[dict[str, Any]] = [
                ow for ow in channel.get("permission_overwrites", []) if isinstance(ow, dict)
            ]

            if not bot_user_id:
                bot_user = self.get_current_user(context="check bot user for permissions")
                bot_user_id = str(bot_user.get("id", ""))

            role_allow = role_deny = member_allow = member_deny = 0
            for ow in overwrites:
                ow_id = str(ow.get("id", ""))
                ow_type = int(ow.get("type", -1))
                ow_allow = int(ow.get("allow", 0))
                ow_deny = int(ow.get("deny", 0))
                if ow_type == 0 and (ow_id == str(guild_id) or ow_id in bot_role_ids):
                    role_allow |= ow_allow
                    role_deny |= ow_deny
                elif ow_type == 1 and ow_id == bot_user_id:
                    member_allow |= ow_allow
                    member_deny |= ow_deny

            permissions = (base_permissions & ~role_deny) | role_allow
            permissions = (permissions & ~member_deny) | member_allow
            return permissions
        except (DiscordApiError, ValueError, TypeError, KeyError):
            return 0

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
