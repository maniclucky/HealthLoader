# `fitthing dashboard`: local HTML charts

2026-10-07, agent (Claude). Follows [[2026-10-06-treadmill-steps]].

The user asked for raw weight plus a 7-day moving average, macro deltas, and
steps, in a local HTML page. `fitthing dashboard` writes
`data/health/dashboard.html` (gitignored under `data/`) and opens it with
`webbrowser`. Code: `src/fitthing/dashboard.py` + `dashboard_template.html`
(package data, `pyproject.toml` `[tool.setuptools.package-data]`).

## Decisions

- **Built from `build_combined_rows`, not by reading `daily.csv`.** The CSV's
  columns depend on `csv_columns`, and its weight is interpolated. Raw weight
  comes from `build_daily_rows` (actual weigh-ins only).
- **7-day MA = mean of actual weigh-ins in the trailing 7 calendar days**,
  using all raw data, so the first days of the window still get a full
  window. Interpolated values are never averaged in.
- **Unlogged nutrition days are blank, not 0.** In `daily.csv` they are
  target-filled and show a delta of 0. Charting that would read as "exactly
  on target." "Logged" = the day is a key in `nutrition/daily.json`.
- **Calories are a separate chart from protein/carbs/fat** (kcal vs g = two
  scales; there is no dual axis). Calories: diverging bars, blue under / red over.
  Macros: three lines with a zero baseline.
- **No CDN.** Hand-rolled inline SVG + JS, so it works offline from `file://`.
  Light/dark via `prefers-color-scheme`. Has hover tooltips and a table view.

## Verified

- 155 tests pass (3 new in `tests/test_dashboard.py`).
- Rendered with headless Firefox (light 1200px, dark 400px). Use a separate
  `--profile` because the user's Firefox is usually running.

## Not done

- `sync` doesn't regenerate the dashboard automatically; run `fitthing dashboard`.
