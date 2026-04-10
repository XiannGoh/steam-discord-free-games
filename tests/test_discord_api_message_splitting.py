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


def test_health_report_labels_stay_within_discord_limit_after_rechunking():
    lines = [f"- check-{index:04d}: {'x' * 32}" for index in range(1000)]
    report = "\n".join(lines)
    chunks = split_discord_content(report)

    assert len(chunks) > 1

    while True:
        total_chunks = len(chunks)
        max_prefix_len = len(f"📋 Bot Health Report ({total_chunks}/{total_chunks})\n")
        reserved_hard_limit = DISCORD_MESSAGE_HARD_LIMIT - max_prefix_len
        assert reserved_hard_limit > 0

        reserved_target_limit = min(1900, reserved_hard_limit)
        rechunked = split_discord_content(
            report,
            target_limit=reserved_target_limit,
            hard_limit=reserved_hard_limit,
        )
        if len(rechunked) == total_chunks:
            chunks = rechunked
            break
        chunks = rechunked

    total_chunks = len(chunks)
    labeled_chunks = [
        f"📋 Bot Health Report ({index}/{total_chunks})\n{chunk}"
        for index, chunk in enumerate(chunks, start=1)
    ]

    assert len(labeled_chunks) > 1
    assert all(len(chunk) <= DISCORD_MESSAGE_HARD_LIMIT for chunk in labeled_chunks)
