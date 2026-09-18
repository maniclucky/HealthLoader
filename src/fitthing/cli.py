"""Command-line entry point for fitthing."""

from __future__ import annotations

import json
import sys
from datetime import date

import requests

from fitthing.auth import ALL_SCOPES, IDENTITY_URL, ensure_signed_in, run_browser_flow
from fitthing.client import ApiError, HealthClient
from fitthing.config import Config, load_config
from fitthing.cronometer.auth import CronometerAuthError, resolve_credentials, sign_in
from fitthing.datatypes import REGISTRY, resolve
from fitthing.export import run_export


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
    subparsers.add_parser("cronometer-auth", help="Sign in to Cronometer (prompts for credentials).")

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
    if resp.status_code >= 400:
        print(f"Google Health API error {resp.status_code}:", file=sys.stderr)
        print(resp.text, file=sys.stderr)
        return 1
    print(json.dumps(resp.json(), indent=2))
    return 0


def cmd_cronometer_auth(args, config: Config) -> int:
    creds = resolve_credentials(config.cronometer, interactive=_interactive(args))
    try:
        sign_in(config.cronometer, creds)
    except CronometerAuthError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Signed in to Cronometer as {creds.username}.")
    return 0


def cmd_types(args, config: Config) -> int:
    creds = ensure_signed_in(config, ALL_SCOPES, interactive=_interactive(args))
    granted_scopes = set(creds.scopes or [])
    for data_type in REGISTRY.values():
        mark = "✔" if data_type.scope in granted_scopes else "✘"
        print(f"{mark} {data_type.id}")
    return 0


def cmd_export(args, config: Config) -> int:
    creds = ensure_signed_in(config, ALL_SCOPES, interactive=_interactive(args))
    granted_scopes = set(creds.scopes or [])

    try:
        requested = args.types.split(",") if args.types else config.data_types
        data_types = resolve(requested)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    since = date.fromisoformat(args.since) if args.since else None
    until = date.fromisoformat(args.until) if args.until else None

    client = HealthClient(creds, request_delay_seconds=config.request_delay_seconds)
    try:
        result = run_export(
            client, config, data_types, granted_scopes, since=since, until=until, full=args.full
        )
    except ApiError as exc:
        print(f"Export stopped: {exc}", file=sys.stderr)
        return 1

    print(
        f"Exported {len(result.exported)} type(s); "
        f"{len(result.skipped_ungranted)} skipped (scope not granted); "
        f"{len(result.failed)} failed.",
        file=sys.stderr,
    )
    return 1 if result.failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    config = load_config()

    if args.command == "auth":
        return cmd_auth(args, config)
    if args.command == "cronometer-auth":
        return cmd_cronometer_auth(args, config)
    if args.command == "whoami":
        return cmd_whoami(args, config)
    if args.command == "types":
        return cmd_types(args, config)
    if args.command == "export":
        return cmd_export(args, config)

    print(f"'{args.command}' is not implemented yet.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
