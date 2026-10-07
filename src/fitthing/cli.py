"""Command-line entry point for fitthing."""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

import requests

from fitthing.auth import ALL_SCOPES, IDENTITY_URL, ensure_signed_in, run_browser_flow
from fitthing.client import ApiError, HealthClient
from fitthing.config import Config, load_config
from fitthing.cronometer.auth import (
    CronometerAuthError,
    resolve_credentials,
    save_credentials,
    sign_in,
)
from fitthing.cronometer.client import CronometerClient, CronometerClientError, ExportCapReached
from fitthing.cronometer.export import MAX_BACKFILL_DAYS as CRONOMETER_MAX_BACKFILL_DAYS
from fitthing.cronometer.export import run_cronometer_export, run_cronometer_import
from fitthing.dashboard import write_dashboard
from fitthing.datatypes import REGISTRY, resolve
from fitthing.export import MAX_BACKFILL_DAYS, run_export
from fitthing.sync import run_sync


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
    export_parser.add_argument(
        "--days",
        type=int,
        help=(
            "Days to backfill on a fresh run with no prior state (default 30, "
            f"max {MAX_BACKFILL_DAYS}). Ignored once state exists, unless --full."
        ),
    )

    cronometer_export_parser = subparsers.add_parser(
        "cronometer-export", help="Download Cronometer daily calories/macros to disk."
    )
    cronometer_export_parser.add_argument("--since", help="Start date (YYYY-MM-DD), overrides state.")
    cronometer_export_parser.add_argument("--until", help="End date (YYYY-MM-DD), default: today.")
    cronometer_export_parser.add_argument(
        "--full", action="store_true", help="Ignore saved state and re-backfill from scratch."
    )
    cronometer_export_parser.add_argument(
        "--days",
        type=int,
        help=(
            "Days to backfill on a fresh run with no prior state (default 30, "
            f"max {CRONOMETER_MAX_BACKFILL_DAYS}). Ignored once state exists, unless --full."
        ),
    )

    sync_parser = subparsers.add_parser(
        "sync",
        help="Pull Cronometer and Google Health, then rebuild data/health/daily.csv.",
    )
    sync_parser.add_argument(
        "--full", action="store_true", help="Ignore saved state and re-backfill both sources."
    )
    sync_parser.add_argument(
        "--days",
        type=int,
        help=(
            "Days to backfill on a fresh run with no prior state (default 30, "
            f"max {min(MAX_BACKFILL_DAYS, CRONOMETER_MAX_BACKFILL_DAYS)}). "
            "Ignored once state exists, unless --full."
        ),
    )

    cronometer_import_parser = subparsers.add_parser(
        "cronometer-import",
        help="Merge a Cronometer Daily Summary CSV you exported by hand.",
    )
    cronometer_import_parser.add_argument("file", help="Path to the exported CSV.")

    dashboard_parser = subparsers.add_parser(
        "dashboard",
        help="Write data/health/dashboard.html (weight, macros, steps) and open it.",
    )
    dashboard_parser.add_argument(
        "--days", type=int, help="Days to chart, ending yesterday (default: csv_days, 30)."
    )
    dashboard_parser.add_argument(
        "--no-open", action="store_true", help="Write the file without opening a browser."
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

    # Per plan §3.2: env-var credentials are the "nothing stored on disk"
    # option, so only persist when they came from the secrets file or a
    # fresh prompt — re-saving file-sourced creds is a harmless no-op.
    from_env = os.environ.get("CRONOMETER_USERNAME") and os.environ.get("CRONOMETER_PASSWORD")
    if not from_env:
        save_credentials(config.cronometer.credentials_path, creds.username, creds.password)

    print(f"Signed in to Cronometer as {creds.username}.")
    return 0


def cmd_cronometer_export(args, config: Config) -> int:
    if args.days is not None and not (1 <= args.days <= CRONOMETER_MAX_BACKFILL_DAYS):
        print(f"--days must be between 1 and {CRONOMETER_MAX_BACKFILL_DAYS}.", file=sys.stderr)
        return 2

    creds = resolve_credentials(config.cronometer, interactive=_interactive(args))
    client = CronometerClient(config.cronometer, creds)

    since = date.fromisoformat(args.since) if args.since else None
    until = date.fromisoformat(args.until) if args.until else None

    try:
        result = run_cronometer_export(
            client, config, since=since, until=until, full=args.full, days=args.days
        )
    except ExportCapReached as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except CronometerAuthError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except CronometerClientError as exc:
        print(f"Cronometer export stopped: {exc}", file=sys.stderr)
        return 1

    print(
        f"Cronometer: {result.days_updated} day(s) updated, range "
        f"{result.range_start.isoformat()}→{result.range_end.isoformat()}.",
        file=sys.stderr,
    )
    return 0


def cmd_cronometer_import(args, config: Config) -> int:
    try:
        days_imported = run_cronometer_import(config, Path(args.file))
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Cronometer: {days_imported} day(s) imported from {args.file}.", file=sys.stderr)
    return 0


def cmd_types(args, config: Config) -> int:
    creds = ensure_signed_in(config, ALL_SCOPES, interactive=_interactive(args))
    granted_scopes = set(creds.scopes or [])
    for data_type in REGISTRY.values():
        mark = "✔" if data_type.scope in granted_scopes else "✘"
        print(f"{mark} {data_type.id}")
    return 0


def cmd_export(args, config: Config) -> int:
    if args.days is not None and not (1 <= args.days <= MAX_BACKFILL_DAYS):
        print(f"--days must be between 1 and {MAX_BACKFILL_DAYS}.", file=sys.stderr)
        return 2

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
            client,
            config,
            data_types,
            granted_scopes,
            since=since,
            until=until,
            full=args.full,
            days=args.days,
        )
    except ApiError as exc:
        print(f"Export stopped: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:  # e.g. a bad csv_columns entry
        print(str(exc), file=sys.stderr)
        return 2

    print(
        f"Exported {len(result.exported)} type(s); "
        f"{len(result.skipped_ungranted)} skipped (scope not granted); "
        f"{len(result.failed)} failed.",
        file=sys.stderr,
    )
    print(f"Wrote {result.csv_days} day(s) to {result.csv_path}.", file=sys.stderr)
    return 1 if result.failed else 0


def cmd_sync(args, config: Config) -> int:
    max_days = min(MAX_BACKFILL_DAYS, CRONOMETER_MAX_BACKFILL_DAYS)
    if args.days is not None and not (1 <= args.days <= max_days):
        print(f"--days must be between 1 and {max_days}.", file=sys.stderr)
        return 2

    try:
        result = run_sync(config, interactive=_interactive(args), full=args.full, days=args.days)
    except ValueError as exc:  # e.g. a bad csv_columns entry
        print(str(exc), file=sys.stderr)
        return 2

    if result.cronometer is not None:
        print(
            f"Cronometer: {result.cronometer.days_updated} day(s) updated, range "
            f"{result.cronometer.range_start.isoformat()}→{result.cronometer.range_end.isoformat()}.",
            file=sys.stderr,
        )
    if result.google is not None:
        print(
            f"Google Health: exported {len(result.google.exported)} type(s); "
            f"{len(result.google.skipped_ungranted)} skipped (scope not granted); "
            f"{len(result.google.failed)} failed.",
            file=sys.stderr,
        )
    print(f"Wrote {result.csv_days} day(s) to {result.csv_path}.", file=sys.stderr)
    return 0 if result.ok else 1


def cmd_dashboard(args, config: Config) -> int:
    if args.days is not None and args.days < 1:
        print("--days must be at least 1.", file=sys.stderr)
        return 2
    path = write_dashboard(config, days=args.days)
    print(f"Wrote {path}.", file=sys.stderr)
    if not args.no_open:
        import webbrowser

        webbrowser.open(path.resolve().as_uri())
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
    if args.command == "cronometer-auth":
        return cmd_cronometer_auth(args, config)
    if args.command == "whoami":
        return cmd_whoami(args, config)
    if args.command == "types":
        return cmd_types(args, config)
    if args.command == "export":
        return cmd_export(args, config)
    if args.command == "cronometer-export":
        return cmd_cronometer_export(args, config)
    if args.command == "sync":
        return cmd_sync(args, config)
    if args.command == "cronometer-import":
        return cmd_cronometer_import(args, config)
    if args.command == "dashboard":
        return cmd_dashboard(args, config)

    print(f"'{args.command}' is not implemented yet.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
