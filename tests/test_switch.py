"""Tests for the switch platform."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.const import EntityCategory
import pytest
from quilt_hp.models.enums import RemoteSensorControlMode

from custom_components.quilt_hp.const import DOMAIN
from custom_components.quilt_hp.switch import (
    QuiltDialTemperatureSwitch,
    QuiltScheduleSwitch,
    async_setup_entry,
)

from .conftest import (
    make_controller,
    make_location,
    make_mock_coordinator,
    make_snapshot,
)


@pytest.fixture
def coordinator(hass):
    snapshot = make_snapshot(locations=[make_location(schedule_paused=False)])
    return make_mock_coordinator(hass, snapshot)


@pytest.fixture
def coordinator_paused(hass):
    snapshot = make_snapshot(locations=[make_location(schedule_paused=True)])
    return make_mock_coordinator(hass, snapshot)


def test_schedule_switch_is_on_when_running(coordinator) -> None:
    entity = QuiltScheduleSwitch(coordinator, "loc-001")
    assert entity.is_on is True


def test_schedule_switch_is_off_when_paused(coordinator_paused) -> None:
    entity = QuiltScheduleSwitch(coordinator_paused, "loc-001")
    assert entity.is_on is False


def test_schedule_switch_unique_id(coordinator) -> None:
    entity = QuiltScheduleSwitch(coordinator, "loc-001")
    assert entity.unique_id == "quilt_schedule_loc-001"


@pytest.mark.asyncio
async def test_schedule_switch_turn_off_pauses(coordinator) -> None:
    entity = QuiltScheduleSwitch(coordinator, "loc-001")
    await entity.async_turn_off()
    coordinator.async_set_schedule_execution.assert_awaited_once_with(paused=True)
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_schedule_switch_turn_on_resumes(coordinator_paused) -> None:
    entity = QuiltScheduleSwitch(coordinator_paused, "loc-001")
    await entity.async_turn_on()
    coordinator_paused.async_set_schedule_execution.assert_awaited_once_with(
        paused=False
    )
    coordinator_paused.async_request_refresh.assert_awaited_once()


def test_schedule_switch_device_info(coordinator) -> None:
    entity = QuiltScheduleSwitch(coordinator, "loc-001")
    assert (DOMAIN, "loc_loc-001") in entity.device_info["identifiers"]


def test_schedule_switch_unavailable_when_location_removed(coordinator) -> None:
    entity = QuiltScheduleSwitch(coordinator, "loc-001")
    assert entity.available
    coordinator.location_by_id = {}
    assert not entity.available


# ── Dial temperature sensor switch ────────────────────────────────────────────


@pytest.fixture
def dial_coordinator(hass):
    ctrl = make_controller()
    ctrl.remote_sensor_mode = RemoteSensorControlMode.ENABLED
    return make_mock_coordinator(hass, make_snapshot(controllers=[ctrl]))


async def test_async_setup_entry_adds_dial_switch(hass, dial_coordinator) -> None:
    entry = MagicMock()
    entry.runtime_data = dial_coordinator
    entities = []
    await async_setup_entry(hass, entry, entities.extend)
    assert {type(e) for e in entities} == {
        QuiltScheduleSwitch,
        QuiltDialTemperatureSwitch,
    }


def test_dial_temperature_switch_state(dial_coordinator) -> None:
    entity = QuiltDialTemperatureSwitch(dial_coordinator, "ctrl-001")
    assert entity.unique_id == "quilt_ctrl_ctrl-001_use_dial_temperature"
    assert entity.entity_category is EntityCategory.CONFIG
    assert entity.is_on is True

    ctrl = dial_coordinator.ctrl_by_id["ctrl-001"]
    ctrl.remote_sensor_mode = RemoteSensorControlMode.DISABLED
    assert entity.is_on is False
    ctrl.remote_sensor_mode = RemoteSensorControlMode.UNSPECIFIED
    assert entity.is_on is None


@pytest.mark.asyncio
async def test_dial_temperature_switch_turn_off(dial_coordinator) -> None:
    entity = QuiltDialTemperatureSwitch(dial_coordinator, "ctrl-001")
    dial_coordinator.is_streaming = True
    await entity.async_turn_off()
    dial_coordinator.async_set_controller.assert_awaited_once_with(
        dial_coordinator.ctrl_by_id["ctrl-001"], uses_dial_temperature=False
    )
    dial_coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_dial_temperature_switch_turn_on_polls_without_stream(
    dial_coordinator,
) -> None:
    entity = QuiltDialTemperatureSwitch(dial_coordinator, "ctrl-001")
    dial_coordinator.is_streaming = False
    await entity.async_turn_on()
    dial_coordinator.async_set_controller.assert_awaited_once_with(
        dial_coordinator.ctrl_by_id["ctrl-001"], uses_dial_temperature=True
    )
    dial_coordinator.async_request_refresh.assert_awaited_once()


def test_dial_temperature_switch_available_when_dial_offline(hass) -> None:
    """The setting is Quilt's, so it can be changed while the Dial is offline."""
    ctrl = make_controller(online=False)
    ctrl.remote_sensor_mode = RemoteSensorControlMode.ENABLED
    coordinator = make_mock_coordinator(hass, make_snapshot(controllers=[ctrl]))
    entity = QuiltDialTemperatureSwitch(coordinator, "ctrl-001")
    assert entity.available is True
    assert entity.is_on is True


def test_dial_temperature_switch_unavailable_when_setting_unknown(
    dial_coordinator,
) -> None:
    entity = QuiltDialTemperatureSwitch(dial_coordinator, "ctrl-001")
    ctrl = dial_coordinator.ctrl_by_id["ctrl-001"]
    ctrl.remote_sensor_mode = RemoteSensorControlMode.UNSPECIFIED
    assert entity.available is False


def test_dial_temperature_switch_unavailable_when_dial_deleted(
    dial_coordinator,
) -> None:
    entity = QuiltDialTemperatureSwitch(dial_coordinator, "ctrl-001")
    dial_coordinator.ctrl_by_id = {}
    assert entity.available is False
