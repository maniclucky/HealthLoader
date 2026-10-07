import json
from datetime import date

from fitthing.dashboard import build_dashboard_data, moving_average, write_dashboard
from tests.test_aggregate import _write_daily_nutrition, make_config


def test_moving_average_uses_only_weigh_ins_inside_the_window():
    measured = {date(2026, 1, 1): 200.0, date(2026, 1, 5): 202.0, date(2026, 1, 7): 204.0}

    # Jan 1 is 7 days before Jan 8, just outside the 7-day window.
    assert moving_average(measured, date(2026, 1, 8)) == 203.0
    assert moving_average(measured, date(2026, 1, 7)) == 202.0
    assert moving_average(measured, date(2025, 12, 31)) is None


def test_unlogged_nutrition_days_are_blank_not_zero(tmp_path):
    config = make_config(tmp_path, csv_days=2, target_settings={"calories": 2000, "protein": 150})
    _write_daily_nutrition(config.output_dir, {"2026-01-02": {"calories_kcal": 2300, "protein_g": 140}})

    days = build_dashboard_data(config, end=date(2026, 1, 2))["days"]

    assert [d["date"] for d in days] == ["2026-01-01", "2026-01-02"]
    assert days[0]["kcal"] is None and days[0]["protein"] is None
    assert days[1]["kcal"] == 300 and days[1]["protein"] == -10


def test_write_dashboard_embeds_the_data(tmp_path):
    config = make_config(tmp_path, csv_days=30)

    path = write_dashboard(config, days=3, end=date(2026, 1, 10))

    html = path.read_text()
    assert "/*__DATA__*/" not in html
    payload = html.split("const DATA = ", 1)[1].split(";\n", 1)[0]
    assert len(json.loads(payload)["days"]) == 3
