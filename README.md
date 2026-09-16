# FitThing

A Python command-line tool (`fitthing`) that signs in with your Google account
and downloads your logged health data from the Google Health API to your own
computer as JSON files.

Full setup and usage instructions will be added as the tool is built (see
`agent-notes/2026-09-16-google-health-export-plan.md` for the build plan and
current status).

## Repository layout

- `agent-notes/` — shared reference space for AI agents (and humans). See `agent-notes/README.md`.
- `src/fitthing/` — the `fitthing` package.
- `tests/` — pytest suite (no network calls).
- `config.example.toml` — copy to `config.toml` (gitignored) and edit.
- `secrets/`, `data/` — gitignored; created locally, never committed.
