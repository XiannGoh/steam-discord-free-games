from discord_api import DISCORD_MESSAGE_HARD_LIMIT, split_discord_content


def test_split_discord_content_keeps_short_message_single_chunk():
    message = "Hello\n\nWorld"
    chunks = split_discord_content(message)
    assert chunks == [message]


def test_split_discord_content_splits_long_message_without_exceeding_limit():
    lines = [f"- user-{index:04d}" for index in range(1500)]
    message = "\n".join(lines)
    chunks = split_discord_content(message)

    assert len(chunks) > 1
    assert all(len(chunk) <= DISCORD_MESSAGE_HARD_LIMIT for chunk in chunks)
    assert "".join(chunks).replace("\n", "") in message.replace("\n", "")

