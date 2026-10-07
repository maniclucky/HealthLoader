"""Render a self-contained HTML dashboard from the data daily.csv is built from.

Charts: weight (actual weigh-ins + 7-day moving average), calories vs target,
protein/carbs/fat vs target, and steps vs target. The page has no external
dependencies (no CDN), so it opens offline straight from disk.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import date, datetime, timedelta
from importlib import resources
from pathlib import Path

from fitthing.aggregate import (
    STEPS_COLUMN,
    TARGET_DELTAS,
    WEIGHT_COLUMN,
    _load_nutrition,
    _load_target_settings,
    build_combined_rows,
    build_daily_rows,
)
from fitthing.config import Config

DASHBOARD_PATH = Path("health") / "dashboard.html"
MOVING_AVERAGE_DAYS = 7
DATA_PLACEHOLDER = "/*__DATA__*/null"

# Dashboard key -> targetSettings.json key (see aggregate.TARGET_DELTAS).
NUTRITION_DELTAS = {"kcal": "calories", "protein": "protein", "carbs": "carbs", "fat": "fat"}


def moving_average(measured: dict[date, float], day: date, window: int = MOVING_AVERAGE_DAYS) -> float | None:
    """Mean of the actual weigh-ins in the `window` days ending on `day`.

    Only measured days count (never interpolated ones), so a stretch with
    few weigh-ins averages fewer points rather than invented ones. None if
    there's no weigh-in in the window at all.
    """
    values = [measured[d] for d in (day - timedelta(days=n) for n in range(window)) if d in measured]
    return sum(values) / len(values) if values else None


def build_dashboard_data(config: Config, end: date | None = None) -> dict:
    rows = build_combined_rows(config, end=end)
    google_rows = build_daily_rows(config.raw_dir, config.csv_device_packages, config.csv_treadmill_packages)
    measured = {d: r[WEIGHT_COLUMN] for d, r in google_rows.items() if r.get(WEIGHT_COLUMN) is not None}
    logged = set(_load_nutrition(config.output_dir / "nutrition" / "daily.json"))
    targets = _load_target_settings(config.target_settings_path)

    days = []
    for day in sorted(rows):
        row = rows[day]
        entry = {
            "date": day.isoformat(),
            "weight": measured.get(day),
            "weightAvg": moving_average(measured, day),
            "steps": row[STEPS_COLUMN],
            "logged": day in logged,
        }
        # Unlogged days are target-filled in daily.csv (delta 0); here
        # they're left blank so a gap isn't drawn as "exactly on target".
        for key, setting_key in NUTRITION_DELTAS.items():
            _, delta_column = TARGET_DELTAS[setting_key]
            entry[key] = row[delta_column] if day in logged else None
        days.append(entry)

    return {
        "generated": datetime.now().isoformat(timespec="minutes"),
        "movingAverageDays": MOVING_AVERAGE_DAYS,
        "targets": {k: targets.get(k) for k in ("calories", "protein", "carbs", "fat", "steps")},
        "days": days,
    }


def write_dashboard(config: Config, days: int | None = None, end: date | None = None) -> Path:
    """Write output_dir/health/dashboard.html; `days` overrides config.csv_days."""
    if days is not None:
        config = replace(config, csv_days=days)
    data = build_dashboard_data(config, end=end)
    # "</" inside the inline <script> would end it early.
    payload = json.dumps(data).replace("</", "<\\/")
    template = resources.files("fitthing").joinpath("dashboard_template.html").read_text(encoding="utf-8")
    html = template.replace(DATA_PLACEHOLDER, payload)

    path = config.output_dir / DASHBOARD_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(html, encoding="utf-8")
    os.replace(tmp_path, path)
    return path
