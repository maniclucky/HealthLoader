import pytest

from fitthing.auth import ALL_SCOPES
from fitthing.datatypes import REGISTRY, resolve


def test_food_and_food_measurement_unit_excluded():
    assert "food" not in REGISTRY
    assert "food-measurement-unit" not in REGISTRY


def test_every_scope_is_one_of_the_six_granted_scopes():
    for data_type in REGISTRY.values():
        assert data_type.scope in ALL_SCOPES


def test_sleep_and_exercise_use_small_page_size():
    assert REGISTRY["sleep"].page_size == 25
    assert REGISTRY["exercise"].page_size == 25


def test_other_types_use_large_page_size():
    assert REGISTRY["steps"].page_size == 10_000


def test_resolve_all_returns_everything():
    assert resolve("all") == list(REGISTRY.values())


def test_resolve_list_of_ids():
    result = resolve(["steps", "weight"])
    assert [dt.id for dt in result] == ["steps", "weight"]


def test_resolve_unknown_id_raises():
    with pytest.raises(ValueError, match="bogus-type"):
        resolve(["steps", "bogus-type"])
