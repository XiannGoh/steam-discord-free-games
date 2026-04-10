import argparse

from gaming_library import manage_library


def _parse_users(value: str) -> list[str]:
    if not value:
        return []
    return [token.strip() for token in value.split(",") if token.strip()]


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
