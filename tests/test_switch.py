"""Tests for the switch platform."""

from __future__ import annotations

import pytest

from custom_components.quilt_hp.const import DOMAIN
from custom_components.quilt_hp.switch import QuiltScheduleSwitch

from .conftest import (
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
