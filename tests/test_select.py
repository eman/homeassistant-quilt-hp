"""Tests for the select platform (louver mode and angle)."""

from __future__ import annotations

import pytest
from quilt_hp.models.enums import FanSpeed, LouverAngle, LouverMode

from custom_components.quilt_hp.const import DOMAIN
from custom_components.quilt_hp.select import (
    QuiltFanSpeedSelect,
    QuiltLouverAngleSelect,
    QuiltLouverModeSelect,
)

from .conftest import make_idu, make_mock_coordinator, make_snapshot


@pytest.fixture
def coordinator_sweep(hass):
    idu = make_idu(louver_mode=LouverMode.SWEEP)
    snapshot = make_snapshot(indoor_units=[idu])
    return make_mock_coordinator(hass, snapshot)


@pytest.fixture
def coordinator_fixed(hass):
    idu = make_idu(
        louver_mode=LouverMode.FIXED, louver_fixed_position=LouverAngle.ANGLE3.to_wire()
    )
    snapshot = make_snapshot(indoor_units=[idu])
    return make_mock_coordinator(hass, snapshot)


# ── Fan speed ─────────────────────────────────────────────────────────────────


def test_fan_speed_options(hass) -> None:
    coordinator = make_mock_coordinator(hass, make_snapshot(indoor_units=[make_idu()]))
    entity = QuiltFanSpeedSelect(coordinator, "idu-001")
    assert entity.options == ["auto", "quiet", "low", "medium", "high", "blast"]


def test_fan_speed_current_option_auto(hass) -> None:
    idu = make_idu(fan_speed=FanSpeed.AUTO)
    coordinator = make_mock_coordinator(hass, make_snapshot(indoor_units=[idu]))
    entity = QuiltFanSpeedSelect(coordinator, "idu-001")
    assert entity.current_option == "auto"


def test_fan_speed_current_option_speed(hass) -> None:
    idu = make_idu(fan_speed=FanSpeed.MEDIUM)
    coordinator = make_mock_coordinator(hass, make_snapshot(indoor_units=[idu]))
    entity = QuiltFanSpeedSelect(coordinator, "idu-001")
    assert entity.current_option == "medium"


async def test_fan_speed_select(hass) -> None:
    coordinator = make_mock_coordinator(hass, make_snapshot(indoor_units=[make_idu()]))
    entity = QuiltFanSpeedSelect(coordinator, "idu-001")
    await entity.async_select_option("high")
    call_kwargs = coordinator.async_set_indoor_unit.call_args[1]
    assert call_kwargs["fan_speed"] == FanSpeed.HIGH


def test_fan_speed_unique_id(hass) -> None:
    coordinator = make_mock_coordinator(hass, make_snapshot(indoor_units=[make_idu()]))
    entity = QuiltFanSpeedSelect(coordinator, "idu-001")
    assert entity.unique_id == "quilt_idu_fan_speed_idu-001"


# ── Louver mode ───────────────────────────────────────────────────────────────


def test_louver_mode_options(coordinator_sweep) -> None:
    entity = QuiltLouverModeSelect(coordinator_sweep, "idu-001")
    assert entity.options == ["closed", "sweep", "fixed", "auto"]


def test_louver_mode_current_option(coordinator_sweep) -> None:
    entity = QuiltLouverModeSelect(coordinator_sweep, "idu-001")
    assert entity.current_option == "sweep"


async def test_louver_mode_select(coordinator_sweep) -> None:
    entity = QuiltLouverModeSelect(coordinator_sweep, "idu-001")
    await entity.async_select_option("auto")
    call_kwargs = coordinator_sweep.async_set_indoor_unit.call_args[1]
    assert call_kwargs["louver_mode"] == LouverMode.AUTO


def test_louver_mode_unique_id(coordinator_sweep) -> None:
    entity = QuiltLouverModeSelect(coordinator_sweep, "idu-001")
    assert entity.unique_id == "quilt_idu_louver_mode_idu-001"


# ── Louver angle ──────────────────────────────────────────────────────────────


def test_louver_angle_options(coordinator_fixed) -> None:
    entity = QuiltLouverAngleSelect(coordinator_fixed, "idu-001")
    assert entity.options == [
        "horizontal",
        "slightly_down",
        "down",
        "mostly_down",
        "straight_down",
    ]


def test_louver_angle_current_option(coordinator_fixed) -> None:
    entity = QuiltLouverAngleSelect(coordinator_fixed, "idu-001")
    assert entity.current_option == "down"


def test_louver_angle_available_when_not_fixed(coordinator_sweep) -> None:
    entity = QuiltLouverAngleSelect(coordinator_sweep, "idu-001")
    assert entity.available


async def test_louver_angle_select(coordinator_fixed) -> None:
    entity = QuiltLouverAngleSelect(coordinator_fixed, "idu-001")
    await entity.async_select_option("straight_down")
    call_kwargs = coordinator_fixed.async_set_indoor_unit.call_args[1]
    assert call_kwargs["louver_mode"] == LouverMode.FIXED
    assert abs(call_kwargs["louver_position"] - LouverAngle.ANGLE5.to_wire()) < 0.01


def test_louver_angle_unique_id(coordinator_fixed) -> None:
    entity = QuiltLouverAngleSelect(coordinator_fixed, "idu-001")
    assert entity.unique_id == "quilt_idu_louver_angle_idu-001"


# ── Availability / device info ────────────────────────────────────────────────


def test_device_info(coordinator_fixed) -> None:
    entity = QuiltLouverAngleSelect(coordinator_fixed, "idu-001")
    assert (DOMAIN, "i_idu-001") in entity.device_info["identifiers"]


def test_unavailable_when_offline(hass) -> None:
    idu = make_idu(online=False)
    coordinator = make_mock_coordinator(hass, make_snapshot(indoor_units=[idu]))
    entity = QuiltLouverModeSelect(coordinator, "idu-001")
    assert not entity.available
