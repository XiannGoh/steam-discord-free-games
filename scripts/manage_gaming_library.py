import argparse

from gaming_library import manage_library, normalize_user_id_token


def _parse_users(value: str) -> list[str]:
    if not value:
        return []
    normalized: list[str] = []
    invalid_tokens: list[str] = []
    for token in value.split(","):
        raw = token.strip()
        if not raw:
            continue
        user_id = normalize_user_id_token(raw)
        if user_id:
            normalized.append(user_id)
        else:
            invalid_tokens.append(raw)
    if invalid_tokens:
        invalid_display = ", ".join(invalid_tokens)
        raise RuntimeError(
            "Unsupported --user-ids token(s): "
            f"{invalid_display}. Use raw IDs or <@123...>/<@!123...> mention format."
        )
    return normalized


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manual Gaming Library management")
    parser.add_argument("--operation", required=True, choices=["add", "rename", "assign", "unassign", "set_status", "archive", "unarchive"])
    parser.add_argument("--canonical-name", default="")
    parser.add_argument("--url", default="")
    parser.add_argument("--source-type", default="")
    parser.add_argument("--source-section", default="")
    parser.add_argument("--source-caption", default="")
    parser.add_argument("--identity-key", default="")
    parser.add_argument("--user-ids", default="")
    parser.add_argument("--status", default="active", choices=["active", "paused", "dropped"])
    parser.add_argument("--archive", default="")
    args = parser.parse_args()

    archive = None
    if args.archive != "":
        archive = args.archive.lower() in {"1", "true", "yes", "y"}

    manage_library(
        operation=args.operation,
        canonical_name=args.canonical_name,
        url=args.url,
        source_type=args.source_type,
        source_section=args.source_section,
        source_caption=args.source_caption,
        identity_key=args.identity_key,
        user_ids=_parse_users(args.user_ids),
        status=args.status,
        archive=archive,
    )
    print("ok")
