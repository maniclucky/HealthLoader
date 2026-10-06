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
from dataclasses import dataclass, field
from datetime import date as date_cls

KJ_PER_KCAL = 4.184

_DATE_CANDIDATES = ["Date"]
_CALORIES_CANDIDATES = ["Energy (kcal)", "Energy (kJ)", "Energy", "Calories (kcal)", "Calories"]
_PROTEIN_CANDIDATES = ["Protein (g)", "Protein"]
_CARBS_CANDIDATES = ["Carbs (g)", "Carbohydrates (g)", "Carbs"]
_FAT_CANDIDATES = ["Fat (g)", "Total Fat (g)", "Fat"]

_KJ_HEADERS = {"Energy (kJ)"}

# Cronometer's export response carries no charset, so requests decodes it as
# ISO-8859-1 and the UTF-8 "µ" in headers like "Folate (µg)" arrives as "Âµ".
_MOJIBAKE_FIXES = {"Âµ": "µ"}


@dataclass(frozen=True)
class DailyNutrition:
    date: str
    calories_kcal: float | None
    protein_g: float | None
    carbs_g: float | None
    fat_g: float | None
    # Every other numeric column in the export (vitamins, minerals, fiber,
    # ...), keyed by its cleaned-up CSV header. Columns can vary with the
    # account's settings, so these are carried through as-is, not named.
    nutrients: dict[str, float | None] = field(default_factory=dict)


def _match_column(field_name: str, candidates: list[str], headers: list[str]) -> str:
    for candidate in candidates:
        if candidate in headers:
            return candidate
    raise ValueError(
        f"Cronometer CSV is missing a '{field_name}' column (tried: {candidates}). "
        f"Headers actually present: {headers}"
    )


def _clean_header(header: str) -> str:
    for bad, good in _MOJIBAKE_FIXES.items():
        header = header.replace(bad, good)
    return header.strip()


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
        headers = [_clean_header(h) for h in next(reader)]
    except StopIteration:
        return []

    date_col = _match_column("date", _DATE_CANDIDATES, headers)
    calories_col = _match_column("calories", _CALORIES_CANDIDATES, headers)
    protein_col = _match_column("protein", _PROTEIN_CANDIDATES, headers)
    carbs_col = _match_column("carbs", _CARBS_CANDIDATES, headers)
    fat_col = _match_column("fat", _FAT_CANDIDATES, headers)

    core_cols = {date_col, calories_col, protein_col, carbs_col, fat_col}
    extra_cols = [h for h in headers if h not in core_cols]

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
                nutrients=_extra_nutrients(record, extra_cols),
            )
        )
    return results


def _extra_nutrients(record: dict[str, str], columns: list[str]) -> dict[str, float | None]:
    """Numeric values for the non-core columns; non-numeric ones (e.g.
    Cronometer's "Completed" true/false flag) are left out."""
    result: dict[str, float | None] = {}
    for column in columns:
        try:
            result[column] = _parse_float(record.get(column))
        except ValueError:
            continue
    return result
