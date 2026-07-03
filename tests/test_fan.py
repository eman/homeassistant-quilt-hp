"""Tests for the fan platform."""

from __future__ import annotations

import pytest
from quilt_hp.models.enums import FanSpeed

from custom_components.quilt_hp.const import DOMAIN
from custom_components.quilt_hp.fan import QuiltFanEntity, _pct_to_fan_speed

from .conftest import make_idu, make_mock_coordinator, make_snapshot


@pytest.fixture
def coordinator(hass):
    idu = make_idu(fan_speed=FanSpeed.MEDIUM)
    snapshot = make_snapshot(indoor_units=[idu])
    return make_mock_coordinator(hass, snapshot)


@pytest.fixture
def auto_coordinator(hass):
    idu = make_idu(fan_speed=FanSpeed.AUTO)
    snapshot = make_snapshot(indoor_units=[idu])
    return make_mock_coordinator(hass, snapshot)


def test_percentage_medium(coordinator) -> None:
    entity = QuiltFanEntity(coordinator, "idu-001")
    assert entity.percentage == 60


def test_pct_to_fan_speed_thresholds() -> None:
    assert _pct_to_fan_speed(0) == FanSpeed.AUTO
    assert _pct_to_fan_speed(20) == FanSpeed.QUIET
    assert _pct_to_fan_speed(40) == FanSpeed.LOW
    assert _pct_to_fan_speed(60) == FanSpeed.MEDIUM
    assert _pct_to_fan_speed(80) == FanSpeed.HIGH
    assert _pct_to_fan_speed(100) == FanSpeed.BLAST


def test_speed_count_excludes_auto(coordinator) -> None:
    entity = QuiltFanEntity(coordinator, "idu-001")
    assert entity.speed_count == 5


def test_unique_id(coordinator) -> None:
    entity = QuiltFanEntity(coordinator, "idu-001")
    assert entity.unique_id == "quilt_idu_fan_idu-001"


def test_device_info(coordinator) -> None:
    entity = QuiltFanEntity(coordinator, "idu-001")
    assert (DOMAIN, "i_idu-001") in entity.device_info["identifiers"]


# ── AUTO is the off state ─────────────────────────────────────────────────────


def test_auto_is_off_state(auto_coordinator) -> None:
    entity = QuiltFanEntity(auto_coordinator, "idu-001")
    assert entity.is_on is False
    assert entity.percentage == 0
    assert entity.preset_mode is None


def test_is_on_when_explicit_speed(coordinator) -> None:
    entity = QuiltFanEntity(coordinator, "idu-001")
    assert entity.is_on is True


def test_preset_modes_exclude_auto(coordinator) -> None:
    entity = QuiltFanEntity(coordinator, "idu-001")
    assert entity.preset_modes == ["quiet", "low", "medium", "high", "blast"]


def test_preset_mode(coordinator) -> None:
    entity = QuiltFanEntity(coordinator, "idu-001")
    assert entity.preset_mode == "medium"


# ── Writes ────────────────────────────────────────────────────────────────────


async def test_set_percentage(coordinator) -> None:
    entity = QuiltFanEntity(coordinator, "idu-001")
    await entity.async_set_percentage(80)
    coordinator.async_set_indoor_unit.assert_awaited_once()
    call_kwargs = coordinator.async_set_indoor_unit.call_args[1]
    assert call_kwargs["fan_speed"] == FanSpeed.HIGH


async def test_set_percentage_zero_sets_auto(coordinator) -> None:
    entity = QuiltFanEntity(coordinator, "idu-001")
    await entity.async_set_percentage(0)
    call_kwargs = coordinator.async_set_indoor_unit.call_args[1]
    assert call_kwargs["fan_speed"] == FanSpeed.AUTO


async def test_set_preset_mode(coordinator) -> None:
    entity = QuiltFanEntity(coordinator, "idu-001")
    await entity.async_set_preset_mode("blast")
    call_kwargs = coordinator.async_set_indoor_unit.call_args[1]
    assert call_kwargs["fan_speed"] == FanSpeed.BLAST


async def test_turn_off_sets_auto(coordinator) -> None:
    entity = QuiltFanEntity(coordinator, "idu-001")
    await entity.async_turn_off()
    call_kwargs = coordinator.async_set_indoor_unit.call_args[1]
    assert call_kwargs["fan_speed"] == FanSpeed.AUTO


async def test_turn_on_restores_last_explicit_speed(auto_coordinator) -> None:
    entity = QuiltFanEntity(auto_coordinator, "idu-001")
    await entity.async_turn_on()
    call_kwargs = auto_coordinator.async_set_indoor_unit.call_args[1]
    assert call_kwargs["fan_speed"] == FanSpeed.LOW  # default restore speed


async def test_turn_on_with_preset(auto_coordinator) -> None:
    entity = QuiltFanEntity(auto_coordinator, "idu-001")
    await entity.async_turn_on(preset_mode="high")
    call_kwargs = auto_coordinator.async_set_indoor_unit.call_args[1]
    assert call_kwargs["fan_speed"] == FanSpeed.HIGH


async def test_turn_on_with_percentage(auto_coordinator) -> None:
    entity = QuiltFanEntity(auto_coordinator, "idu-001")
    await entity.async_turn_on(percentage=100)
    call_kwargs = auto_coordinator.async_set_indoor_unit.call_args[1]
    assert call_kwargs["fan_speed"] == FanSpeed.BLAST


async def test_turn_on_with_zero_percentage_falls_back_to_explicit(
    auto_coordinator,
) -> None:
    """0 % maps to AUTO which is 'off' — turn_on must pick an explicit speed."""
    entity = QuiltFanEntity(auto_coordinator, "idu-001")
    await entity.async_turn_on(percentage=5)
    call_kwargs = auto_coordinator.async_set_indoor_unit.call_args[1]
    assert call_kwargs["fan_speed"] == FanSpeed.LOW


async def test_last_explicit_speed_remembered(coordinator) -> None:
    entity = QuiltFanEntity(coordinator, "idu-001")
    await entity.async_set_preset_mode("blast")
    await entity.async_turn_off()
    await entity.async_turn_on()
    call_kwargs = coordinator.async_set_indoor_unit.call_args[1]
    assert call_kwargs["fan_speed"] == FanSpeed.BLAST


# ── Availability ──────────────────────────────────────────────────────────────


def test_unavailable_when_offline(hass) -> None:
    idu = make_idu(online=False)
    coordinator = make_mock_coordinator(hass, make_snapshot(indoor_units=[idu]))
    entity = QuiltFanEntity(coordinator, "idu-001")
    assert not entity.available


def test_unavailable_when_idu_removed(coordinator) -> None:
    entity = QuiltFanEntity(coordinator, "idu-001")
    coordinator.idu_by_id = {}
    assert not entity.available
