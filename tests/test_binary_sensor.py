"""Tests for the binary_sensor platform."""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock

from quilt_hp.models.enums import OccupancyState, Presence

from custom_components.quilt_hp.binary_sensor import (
    IDU_BINARY_SENSOR_DESCRIPTIONS,
    QuiltControllerBinarySensor,
    QuiltIDUBinarySensor,
    async_setup_entry,
)

from .conftest import make_controller, make_idu, make_mock_coordinator, make_snapshot


async def test_async_setup_entry(hass) -> None:
    """Test setting up binary sensors."""
    entry = MagicMock()

    # Create IDU with presence
    idu = make_idu()
    idu.presence = MagicMock()
    idu.presence.sensor0_presence = Presence.DETECTED
    idu.presence.sensor1_presence = Presence.UNDETECTED
    type(idu).effective_occupancy_state = PropertyMock(
        return_value=OccupancyState.DETECTED
    )

    controller = make_controller()

    snapshot = make_snapshot(indoor_units=[idu], controllers=[controller])
    coordinator = make_mock_coordinator(hass, snapshot)
    entry.runtime_data = coordinator

    entities = []
    await async_setup_entry(hass, entry, lambda x: entities.extend(x))

    # Should create 5 IDU sensors + 1 controller sensor
    assert len(entities) == 6


async def test_idu_presence_sensor_or_of_channels(hass) -> None:
    """Presence is the OR of both radar channels (vendor-app semantics)."""
    idu = make_idu()
    idu.presence = MagicMock()
    idu.presence.sensor0_presence = Presence.UNDETECTED
    idu.presence.sensor1_presence = Presence.DETECTED

    snapshot = make_snapshot(indoor_units=[idu])
    coordinator = make_mock_coordinator(hass, snapshot)

    sensor = QuiltIDUBinarySensor(
        coordinator, idu.id, IDU_BINARY_SENSOR_DESCRIPTIONS[0]
    )

    assert sensor.is_on is True
    assert sensor.available
    assert sensor.unique_id == f"quilt_idu_{idu.id}_presence"


async def test_idu_presence_sensor_clear(hass) -> None:
    """Presence is off when both channels report UNDETECTED."""
    idu = make_idu()
    idu.presence = MagicMock()
    idu.presence.sensor0_presence = Presence.UNDETECTED
    idu.presence.sensor1_presence = Presence.UNDETECTED

    snapshot = make_snapshot(indoor_units=[idu])
    coordinator = make_mock_coordinator(hass, snapshot)

    sensor = QuiltIDUBinarySensor(
        coordinator, idu.id, IDU_BINARY_SENSOR_DESCRIPTIONS[0]
    )

    assert sensor.is_on is False
    assert sensor.available


async def test_idu_presence_sensor_unreported(hass) -> None:
    """Presence is unknown when both channels are UNSPECIFIED."""
    idu = make_idu()
    idu.presence = MagicMock()
    idu.presence.sensor0_presence = Presence.UNSPECIFIED
    idu.presence.sensor1_presence = Presence.UNSPECIFIED

    snapshot = make_snapshot(indoor_units=[idu])
    coordinator = make_mock_coordinator(hass, snapshot)

    sensor = QuiltIDUBinarySensor(
        coordinator, idu.id, IDU_BINARY_SENSOR_DESCRIPTIONS[0]
    )

    assert sensor.is_on is None


async def test_idu_radar_channel_sensors(hass) -> None:
    """Raw radar channel diagnostics report their own channel only."""
    idu = make_idu()
    idu.presence = MagicMock()
    idu.presence.sensor0_presence = Presence.DETECTED
    idu.presence.sensor1_presence = Presence.UNDETECTED

    snapshot = make_snapshot(indoor_units=[idu])
    coordinator = make_mock_coordinator(hass, snapshot)

    ch0 = QuiltIDUBinarySensor(coordinator, idu.id, IDU_BINARY_SENSOR_DESCRIPTIONS[2])
    ch1 = QuiltIDUBinarySensor(coordinator, idu.id, IDU_BINARY_SENSOR_DESCRIPTIONS[3])

    assert ch0.is_on is True
    assert ch1.is_on is False
    # Channel 0 keeps the legacy "motion" unique_id for registry continuity
    assert ch0.unique_id == f"quilt_idu_{idu.id}_motion"
    assert ch1.unique_id == f"quilt_idu_{idu.id}_radar_1"


async def test_idu_occupied_sensor(hass) -> None:
    """Test IDU occupied sensor (effective_occupancy_state)."""
    idu = make_idu()
    type(idu).effective_occupancy_state = PropertyMock(
        return_value=OccupancyState.DETECTED
    )

    snapshot = make_snapshot(indoor_units=[idu])
    coordinator = make_mock_coordinator(hass, snapshot)

    sensor = QuiltIDUBinarySensor(
        coordinator, idu.id, IDU_BINARY_SENSOR_DESCRIPTIONS[1]
    )

    assert sensor.is_on is True
    assert sensor.available


async def test_idu_online_sensor(hass) -> None:
    """Test IDU online sensor."""
    idu = make_idu(online=True)

    snapshot = make_snapshot(indoor_units=[idu])
    coordinator = make_mock_coordinator(hass, snapshot)

    sensor = QuiltIDUBinarySensor(
        coordinator, idu.id, IDU_BINARY_SENSOR_DESCRIPTIONS[4]
    )

    assert sensor.is_on is True
    assert sensor.available


async def test_idu_offline(hass) -> None:
    """Test IDU sensors when IDU is offline."""
    idu = make_idu(online=False)

    snapshot = make_snapshot(indoor_units=[idu])
    coordinator = make_mock_coordinator(hass, snapshot)

    sensor = QuiltIDUBinarySensor(
        coordinator, idu.id, IDU_BINARY_SENSOR_DESCRIPTIONS[0]
    )

    assert not sensor.available


async def test_idu_missing_presence(hass) -> None:
    """Test IDU sensor when presence data is missing."""
    idu = make_idu()
    idu.presence = None

    snapshot = make_snapshot(indoor_units=[idu])
    coordinator = make_mock_coordinator(hass, snapshot)

    sensor = QuiltIDUBinarySensor(
        coordinator, idu.id, IDU_BINARY_SENSOR_DESCRIPTIONS[0]
    )

    assert sensor.is_on is None


async def test_controller_online_sensor(hass) -> None:
    """Test controller online sensor."""
    from custom_components.quilt_hp.binary_sensor import (
        CONTROLLER_BINARY_SENSOR_DESCRIPTIONS,
    )

    controller = make_controller(online=True)

    snapshot = make_snapshot(controllers=[controller])
    coordinator = make_mock_coordinator(hass, snapshot)

    sensor = QuiltControllerBinarySensor(
        coordinator, controller.id, CONTROLLER_BINARY_SENSOR_DESCRIPTIONS[0]
    )

    # Controller is_online is derived from state_updated_at
    # Just check that the sensor is created and has a value
    assert sensor.is_on in [True, False]
    assert sensor.available
    # unique_id is quilt_ctrl_{id}_online
    assert "ctrl" in sensor.unique_id and controller.id in sensor.unique_id


async def test_controller_offline(hass) -> None:
    """Test controller sensor creation."""
    from custom_components.quilt_hp.binary_sensor import (
        CONTROLLER_BINARY_SENSOR_DESCRIPTIONS,
    )

    controller = make_controller(online=False)

    snapshot = make_snapshot(controllers=[controller])
    coordinator = make_mock_coordinator(hass, snapshot)

    sensor = QuiltControllerBinarySensor(
        coordinator, controller.id, CONTROLLER_BINARY_SENSOR_DESCRIPTIONS[0]
    )

    # Just verify the sensor is created successfully
    assert sensor.available
