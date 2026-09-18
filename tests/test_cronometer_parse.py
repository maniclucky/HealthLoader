from datetime import date
from pathlib import Path

import pytest

from fitthing.cronometer.parse import DailyNutrition, parse_daily_summary

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "cronometer_daily_summary.csv"


def test_parses_real_fixture():
    csv_text = FIXTURE_PATH.read_text()

    rows = parse_daily_summary(csv_text)

    assert rows == [
        DailyNutrition(date="2025-01-01", calories_kcal=2000.00, protein_g=100.00, carbs_g=200.00, fat_g=60.00),
        DailyNutrition(date="2025-01-02", calories_kcal=1750.00, protein_g=80.00, carbs_g=150.00, fat_g=50.00),
        DailyNutrition(date="2025-01-03", calories_kcal=2200.00, protein_g=90.00, carbs_g=180.00, fat_g=55.00),
    ]


def test_empty_export_returns_no_rows():
    csv_text = "Date,Energy (kcal),Protein (g),Carbs (g),Fat (g)\n"

    assert parse_daily_summary(csv_text) == []


def test_missing_macro_column_raises_with_all_headers():
    csv_text = "Date,Energy (kcal),Carbs (g),Fat (g)\n2025-01-01,2000,200,60\n"

    with pytest.raises(ValueError) as exc_info:
        parse_daily_summary(csv_text)

    message = str(exc_info.value)
    assert "protein" in message
    assert "Date" in message and "Energy (kcal)" in message and "Fat (g)" in message


def test_blank_cell_is_none_not_zero():
    csv_text = "Date,Energy (kcal),Protein (g),Carbs (g),Fat (g)\n2025-01-01,2000,,200,60\n"

    rows = parse_daily_summary(csv_text)

    assert rows == [
        DailyNutrition(date="2025-01-01", calories_kcal=2000.0, protein_g=None, carbs_g=200.0, fat_g=60.0)
    ]


def test_kj_account_converts_to_kcal(capsys):
    csv_text = "Date,Energy (kJ),Protein (g),Carbs (g),Fat (g)\n2025-01-01,8368.0,100,200,60\n"

    rows = parse_daily_summary(csv_text)

    assert rows[0].calories_kcal == pytest.approx(8368.0 / 4.184)
    assert "kJ" in capsys.readouterr().err


def test_crlf_line_endings():
    csv_text = "Date,Energy (kcal),Protein (g),Carbs (g),Fat (g)\r\n2025-01-01,2000,100,200,60\r\n"

    rows = parse_daily_summary(csv_text)

    assert rows == [
        DailyNutrition(date="2025-01-01", calories_kcal=2000.0, protein_g=100.0, carbs_g=200.0, fat_g=60.0)
    ]


def test_leading_bom_is_stripped():
    csv_text = "﻿Date,Energy (kcal),Protein (g),Carbs (g),Fat (g)\n2025-01-01,2000,100,200,60\n"

    rows = parse_daily_summary(csv_text)

    assert rows == [
        DailyNutrition(date="2025-01-01", calories_kcal=2000.0, protein_g=100.0, carbs_g=200.0, fat_g=60.0)
    ]


def test_unparseable_date_row_dropped_with_warning(capsys):
    csv_text = (
        "Date,Energy (kcal),Protein (g),Carbs (g),Fat (g)\n"
        "not-a-date,2000,100,200,60\n"
        "2025-01-02,1800,90,180,50\n"
    )

    rows = parse_daily_summary(csv_text)

    assert [r.date for r in rows] == ["2025-01-02"]
    assert "unparseable date" in capsys.readouterr().err


def test_rows_outside_requested_range_are_dropped():
    csv_text = (
        "Date,Energy (kcal),Protein (g),Carbs (g),Fat (g)\n"
        "2025-01-01,2000,100,200,60\n"
        "2025-01-05,1800,90,180,50\n"
        "2025-01-10,1900,95,190,55\n"
    )

    rows = parse_daily_summary(csv_text, start=date(2025, 1, 2), end=date(2025, 1, 8))

    assert [r.date for r in rows] == ["2025-01-05"]


def test_alternate_carb_header_spelling_matches():
    csv_text = "Date,Calories,Protein,Carbohydrates (g),Fat\n2025-01-01,2000,100,200,60\n"

    rows = parse_daily_summary(csv_text)

    assert rows == [
        DailyNutrition(date="2025-01-01", calories_kcal=2000.0, protein_g=100.0, carbs_g=200.0, fat_g=60.0)
    ]
