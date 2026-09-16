"""Command-line entry point for fitthing."""

from __future__ import annotations

import json
import sys

import requests

from fitthing.auth import ALL_SCOPES, IDENTITY_URL, ensure_signed_in, run_browser_flow
from fitthing.config import Config, load_config


def build_parser():
    import argparse

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


def _interactive(args) -> bool:
    return sys.stdin.isatty() and not args.no_prompt


def cmd_auth(args, config: Config) -> int:
    run_browser_flow(config, ALL_SCOPES)
    return 0


def cmd_whoami(args, config: Config) -> int:
    creds = ensure_signed_in(config, ALL_SCOPES, interactive=_interactive(args))
    resp = requests.get(
        IDENTITY_URL,
        headers={"Authorization": f"Bearer {creds.token}"},
        timeout=30,
    )
    resp.raise_for_status()
    print(json.dumps(resp.json(), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    config = load_config()

    if args.command == "auth":
        return cmd_auth(args, config)
    if args.command == "whoami":
        return cmd_whoami(args, config)

    print(f"'{args.command}' is not implemented yet.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
