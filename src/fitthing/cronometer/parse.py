"""Parse Cronometer's Daily Summary CSV export into DailyNutrition records.

Pure, no network. Column names are matched by candidate list, never
hardcoded from a single observed export — see the real header row and unit
settings captured in agent-notes/2026-09-17-cronometer-endpoints.md, and the
"never silently default a macro to 0.0" rule in
agent-notes/2026-09-17-cronometer-nutrition-plan.md §1.2.
"""

from __future__ import annotations

import csv
import io
import sys
from dataclasses import dataclass
from datetime import date as date_cls

KJ_PER_KCAL = 4.184

_DATE_CANDIDATES = ["Date"]
_CALORIES_CANDIDATES = ["Energy (kcal)", "Energy (kJ)", "Energy", "Calories (kcal)", "Calories"]
_PROTEIN_CANDIDATES = ["Protein (g)", "Protein"]
_CARBS_CANDIDATES = ["Carbs (g)", "Carbohydrates (g)", "Carbs"]
_FAT_CANDIDATES = ["Fat (g)", "Total Fat (g)", "Fat"]

_KJ_HEADERS = {"Energy (kJ)"}


@dataclass(frozen=True)
class DailyNutrition:
    date: str
    calories_kcal: float | None
    protein_g: float | None
    carbs_g: float | None
    fat_g: float | None


def _match_column(field_name: str, candidates: list[str], headers: list[str]) -> str:
    for candidate in candidates:
        if candidate in headers:
            return candidate
    raise ValueError(
        f"Cronometer CSV is missing a '{field_name}' column (tried: {candidates}). "
        f"Headers actually present: {headers}"
    )


def _parse_float(raw: str | None) -> float | None:
    if raw is None or raw.strip() == "":
        return None
    return float(raw)


def parse_daily_summary(
    csv_text: str,
    *,
    start: date_cls | None = None,
    end: date_cls | None = None,
) -> list[DailyNutrition]:
    """Parse a Cronometer Daily Summary CSV export into DailyNutrition rows.

    Raises ValueError, naming every header actually present, if a required
    column can't be matched by any of its candidate names — a macro silently
    defaulting to 0.0 must be impossible by construction.

    `start`/`end`, if given, drop rows outside that range (with a warning);
    a row with an unparseable date is dropped the same way.
    """
    text = csv_text.lstrip("﻿")
    reader = csv.reader(io.StringIO(text))
    try:
        headers = next(reader)
    except StopIteration:
        return []

    date_col = _match_column("date", _DATE_CANDIDATES, headers)
    calories_col = _match_column("calories", _CALORIES_CANDIDATES, headers)
    protein_col = _match_column("protein", _PROTEIN_CANDIDATES, headers)
    carbs_col = _match_column("carbs", _CARBS_CANDIDATES, headers)
    fat_col = _match_column("fat", _FAT_CANDIDATES, headers)

    is_kj = calories_col in _KJ_HEADERS
    if is_kj:
        print(
            f"Cronometer account's energy unit is kJ; converting '{calories_col}' to kcal.",
            file=sys.stderr,
        )

    results = []
    for row in reader:
        if not row or all(cell.strip() == "" for cell in row):
            continue
        record = dict(zip(headers, row))

        raw_date = record.get(date_col, "").strip()
        try:
            parsed_date = date_cls.fromisoformat(raw_date)
        except ValueError:
            print(f"Skipping row with unparseable date {raw_date!r}.", file=sys.stderr)
            continue

        if start is not None and parsed_date < start:
            print(f"Skipping {parsed_date} — before the requested range.", file=sys.stderr)
            continue
        if end is not None and parsed_date > end:
            print(f"Skipping {parsed_date} — after the requested range.", file=sys.stderr)
            continue

        calories = _parse_float(record.get(calories_col))
        if calories is not None and is_kj:
            calories = calories / KJ_PER_KCAL

        results.append(
            DailyNutrition(
                date=parsed_date.isoformat(),
                calories_kcal=calories,
                protein_g=_parse_float(record.get(protein_col)),
                carbs_g=_parse_float(record.get(carbs_col)),
                fat_g=_parse_float(record.get(fat_col)),
            )
        )
    return results
