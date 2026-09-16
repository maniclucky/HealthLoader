"""Command-line entry point for fitthing.

Subcommands (auth, whoami, types, export) are wired up in later phases.
This scaffolding stage only needs `--help` and an empty subcommand set
to work so that Phase 1's acceptance check passes.
"""

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fitthing",
        description="Download your logged health data from the Google Health API.",
    )
    parser.add_argument(
        "--no-prompt",
        action="store_true",
        help="Never prompt interactively; fail instead of asking to sign in.",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("auth", help="Sign in with Google (opens a browser).")
    subparsers.add_parser("whoami", help="Show the identity of the signed-in account.")
    subparsers.add_parser("types", help="List data types and whether each is granted.")

    export_parser = subparsers.add_parser("export", help="Download data to disk.")
    export_parser.add_argument("--types", help="Comma-separated data type ids to export.")
    export_parser.add_argument("--since", help="Start date (YYYY-MM-DD), overrides state.")
    export_parser.add_argument("--until", help="End date (YYYY-MM-DD), default: today.")
    export_parser.add_argument(
        "--full", action="store_true", help="Ignore saved state and re-backfill from scratch."
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    print(f"'{args.command}' is not implemented yet.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
