"""Tests for the climate platform."""

from __future__ import annotations

import math

from homeassistant.components.climate import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
import pytest
from quilt_hp.models.enums import (
    HVACMode as QHVACMode,
    HVACState as QHVACState,
)

from custom_components.quilt_hp.climate import QuiltClimateEntity
from custom_components.quilt_hp.const import DOMAIN

from .conftest import (
    make_comfort_setting,
    make_idu,
    make_mock_coordinator,
    make_snapshot,
    make_space,
)


@pytest.fixture
def coordinator(hass):
    space = make_space(
        hvac_mode=QHVACMode.HEAT,
        ambient_temp_c=21.0,
        heat_setpoint_c=22.0,
        cool_setpoint_c=25.0,
    )
    snapshot = make_snapshot(spaces=[space])
    return make_mock_coordinator(hass, snapshot)


def _entity(coordinator) -> QuiltClimateEntity:
    return QuiltClimateEntity(coordinator, "space-001", "idu-001")


def test_hvac_mode_heat(coordinator) -> None:
    assert _entity(coordinator).hvac_mode == HVACMode.HEAT


def test_hvac_mode_dry(hass) -> None:
    space = make_space(hvac_mode=QHVACMode.DRY)
    coordinator = make_mock_coordinator(hass, make_snapshot(spaces=[space]))
    assert _entity(coordinator).hvac_mode == HVACMode.DRY


def test_hvac_mode_off_when_standby(hass) -> None:
    space = make_space(hvac_mode=QHVACMode.STANDBY)
    coordinator = make_mock_coordinator(hass, make_snapshot(spaces=[space]))
    entity = _entity(coordinator)
    assert entity.hvac_mode == HVACMode.OFF


def test_current_temperature(coordinator) -> None:
    assert _entity(coordinator).current_temperature == 21.0


def test_current_temperature_missing_ambient_returns_none(hass) -> None:
    space = make_space(ambient_temp_c=21.0)
    space.state.ambient_temperature_c = math.nan
    coordinator = make_mock_coordinator(hass, make_snapshot(spaces=[space]))
    assert _entity(coordinator).current_temperature is None


def test_target_temperature_heat_mode(coordinator) -> None:
    assert _entity(coordinator).target_temperature == 22.0


def test_target_temperature_range(hass) -> None:
    space = make_space(
        hvac_mode=QHVACMode.AUTO, heat_setpoint_c=18.0, cool_setpoint_c=26.0
    )
    coordinator = make_mock_coordinator(hass, make_snapshot(spaces=[space]))
    entity = _entity(coordinator)
    assert entity.target_temperature_low == 18.0
    assert entity.target_temperature_high == 26.0


def test_target_temperature_hidden_in_fan_mode(hass) -> None:
    space = make_space(
        hvac_mode=QHVACMode.FAN, heat_setpoint_c=18.0, cool_setpoint_c=26.0
    )
    coordinator = make_mock_coordinator(hass, make_snapshot(spaces=[space]))
    entity = _entity(coordinator)
    assert entity.target_temperature is None
    assert entity.target_temperature_low is None
    assert entity.target_temperature_high is None


def test_target_temperature_hidden_in_dry_mode(hass) -> None:
    space = make_space(
        hvac_mode=QHVACMode.DRY, heat_setpoint_c=18.0, cool_setpoint_c=26.0
    )
    coordinator = make_mock_coordinator(hass, make_snapshot(spaces=[space]))
    entity = _entity(coordinator)
    assert entity.target_temperature is None
    assert entity.target_temperature_low is None
    assert entity.target_temperature_high is None


def test_target_temperature_hidden_when_standby_sentinel(hass) -> None:
    space = make_space(
        hvac_mode=QHVACMode.STANDBY, heat_setpoint_c=8.0, cool_setpoint_c=40.0
    )
    coordinator = make_mock_coordinator(hass, make_snapshot(spaces=[space]))
    entity = _entity(coordinator)
    assert entity.target_temperature is None
    assert entity.target_temperature_low is None
    assert entity.target_temperature_high is None


def test_target_temperature_shown_when_not_off_even_with_sentinel_values(hass) -> None:
    space = make_space(
        hvac_mode=QHVACMode.HEAT, heat_setpoint_c=8.0, cool_setpoint_c=40.0
    )
    coordinator = make_mock_coordinator(hass, make_snapshot(spaces=[space]))
    entity = _entity(coordinator)
    assert entity.target_temperature == 8.0
    assert entity.target_temperature_low == 8.0
    assert entity.target_temperature_high == 40.0


def test_target_temperature_prefers_active_comfort_setting_when_controls_sentinel(
    hass,
) -> None:
    space = make_space(
        hvac_mode=QHVACMode.AUTO, heat_setpoint_c=8.0, cool_setpoint_c=40.0
    )
    cs = make_comfort_setting(heat_setpoint_c=19.5, cool_setpoint_c=25.5)
    snapshot = make_snapshot(spaces=[space], comfort_settings=[cs])
    coordinator = make_mock_coordinator(hass, snapshot)
    entity = _entity(coordinator)
    assert entity.target_temperature_low == 19.5
    assert entity.target_temperature_high == 25.5


def test_target_temperature_sentinel_falls_back_to_state_setpoint(hass) -> None:
    """When controls AND comfort setting are placeholders, use state setpoint."""
    space = make_space(
        hvac_mode=QHVACMode.HEAT, heat_setpoint_c=8.0, cool_setpoint_c=40.0
    )
    space.state.setpoint_c = 21.5
    cs = make_comfort_setting(heat_setpoint_c=8.0, cool_setpoint_c=40.0)
    snapshot = make_snapshot(spaces=[space], comfort_settings=[cs])
    coordinator = make_mock_coordinator(hass, snapshot)
    assert _entity(coordinator).target_temperature == 21.5


@pytest.mark.parametrize(
    "state",
    [QHVACState.DRY, QHVACState.DRY_DEFERRED, QHVACState.DRY_PREPARING],
)
def test_hvac_action_drying_for_dry_states(hass, state) -> None:
    space = make_space(hvac_mode=QHVACMode.DRY, hvac_state=state)
    coordinator = make_mock_coordinator(hass, make_snapshot(spaces=[space]))
    assert _entity(coordinator).hvac_action == HVACAction.DRYING


async def test_set_hvac_mode(coordinator) -> None:
    entity = _entity(coordinator)
    await entity.async_set_hvac_mode(HVACMode.COOL)
    coordinator.async_set_space.assert_awaited_once()
    call_kwargs = coordinator.async_set_space.call_args[1]
    assert call_kwargs["mode"] == QHVACMode.COOL


async def test_set_temperature_single_heat(coordinator) -> None:
    entity = _entity(coordinator)
    # Coordinator fixture sets mode to HEAT by default
    await entity.async_set_temperature(temperature=23.0)
    coordinator.async_set_space.assert_awaited_once()
    call_kwargs = coordinator.async_set_space.call_args[1]
    assert call_kwargs["heat_setpoint_c"] == 23.0


async def test_set_temperature_single_cool(hass) -> None:
    space = make_space(hvac_mode=QHVACMode.COOL)
    coordinator = make_mock_coordinator(hass, make_snapshot(spaces=[space]))
    entity = _entity(coordinator)
    await entity.async_set_temperature(temperature=24.0)
    coordinator.async_set_space.assert_awaited_once()
    call_kwargs = coordinator.async_set_space.call_args[1]
    assert call_kwargs["cool_setpoint_c"] == 24.0


async def test_set_temperature_range(coordinator) -> None:
    entity = _entity(coordinator)
    await entity.async_set_temperature(target_temp_low=19.0, target_temp_high=27.0)
    coordinator.async_set_space.assert_awaited_once()
    call_kwargs = coordinator.async_set_space.call_args[1]
    assert call_kwargs["heat_setpoint_c"] == 19.0
    assert call_kwargs["cool_setpoint_c"] == 27.0


@pytest.mark.parametrize(
    ("state", "action"),
    [
        (QHVACState.HEAT, HVACAction.HEATING),
        (QHVACState.COOL, HVACAction.COOLING),
        (QHVACState.FAN, HVACAction.FAN),
        (QHVACState.STANDBY, HVACAction.IDLE),
    ],
)
def test_hvac_action_mapping(hass, state, action) -> None:
    space = make_space(hvac_state=state)
    coordinator = make_mock_coordinator(hass, make_snapshot(spaces=[space]))
    assert _entity(coordinator).hvac_action == action


def test_supported_features_include_turn_on_off(coordinator) -> None:
    features = _entity(coordinator).supported_features
    assert features & ClimateEntityFeature.TURN_ON
    assert features & ClimateEntityFeature.TURN_OFF


def test_unique_id(coordinator) -> None:
    assert _entity(coordinator).unique_id == "quilt_space_climate_space-001"


def test_device_info(coordinator) -> None:
    info = _entity(coordinator).device_info
    assert (DOMAIN, "i_idu-001") in info["identifiers"]


# ── Availability ──────────────────────────────────────────────────────────────


def test_available_when_space_and_online_idu(coordinator) -> None:
    assert _entity(coordinator).available is True


def test_unavailable_when_space_missing(coordinator) -> None:
    entity = _entity(coordinator)
    coordinator.spaces_by_id = {}
    assert entity.available is False


def test_unavailable_when_idu_missing(coordinator) -> None:
    entity = _entity(coordinator)
    coordinator.idu_by_id = {}
    assert entity.available is False


def test_unavailable_when_idu_offline(hass) -> None:
    snapshot = make_snapshot(indoor_units=[make_idu(online=False)])
    coordinator = make_mock_coordinator(hass, snapshot)
    assert _entity(coordinator).available is False


def test_unavailable_when_coordinator_failed(coordinator) -> None:
    entity = _entity(coordinator)
    coordinator.last_update_success = False
    assert entity.available is False
