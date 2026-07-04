"""Shared test fixtures for the Quilt Heat Pump integration tests."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
import pytest
from quilt_hp.models.comfort import ComfortSetting
from quilt_hp.models.controller import Controller
from quilt_hp.models.enums import (
    ComfortSettingType,
    FanSpeed,
    HVACMode,
    HVACState,
    LedAnimation,
    LightState,
    LouverMode,
    OccupancyMode,
    RemoteSensorControlMode,
    SafetyHeatingMode,
)
from quilt_hp.models.indoor_unit import (
    IndoorUnit,
    IndoorUnitControls,
    IndoorUnitSettings,
    IndoorUnitState,
)
from quilt_hp.models.outdoor_unit import OutdoorUnit, OutdoorUnitPerformanceData
from quilt_hp.models.qsm import QsmSensors, QuiltSmartModule
from quilt_hp.models.sensor import ControllerRemoteSensor, RemoteSensor
from quilt_hp.models.space import Space, SpaceControls, SpaceSettings, SpaceState
from quilt_hp.models.system import Location

# ── Model helpers ─────────────────────────────────────────────────────────────


def make_space(
    space_id: str = "space-001",
    system_id: str = "sys-001",
    name: str = "Living Room",
    parent_space_id: str = "root-001",
    hvac_mode: HVACMode = HVACMode.HEAT,
    hvac_state: HVACState = HVACState.HEAT,
    ambient_temp_c: float = 21.0,
    heat_setpoint_c: float = 22.0,
    cool_setpoint_c: float = 25.0,
) -> Space:
    return Space(
        id=space_id,
        system_id=system_id,
        name=name,
        parent_space_id=parent_space_id,
        settings=SpaceSettings(
            name=name,
            timezone="America/Los_Angeles",
            occupancy_mode=OccupancyMode.DISABLED,
            occupied_timeout_s=180,
            unoccupied_timeout_s=1200,
            safety_heating=SafetyHeatingMode.ENABLED,
        ),
        controls=SpaceControls(
            hvac_mode=hvac_mode,
            temperature_setpoint_c=heat_setpoint_c,
            cooling_setpoint_c=cool_setpoint_c,
            heating_setpoint_c=heat_setpoint_c,
            comfort_setting_id="cs-001",
            comfort_setting_override=0,
        ),
        state=SpaceState(
            ambient_temperature_c=ambient_temp_c,
            hvac_state=hvac_state,
            setpoint_c=heat_setpoint_c,
            comfort_setting_id="cs-001",
        ),
    )


def make_idu(
    idu_id: str = "idu-001",
    system_id: str = "sys-001",
    space_id: str = "space-001",
    outdoor_unit_id: str | None = "odu-001",
    online: bool = True,
    fan_speed: FanSpeed = FanSpeed.AUTO,
    louver_mode: LouverMode = LouverMode.AUTO,
    louver_fixed_position: float = 0.0,
    led_on: bool = True,
    led_brightness: float = 0.8,
    led_color_code: int = 0xFF460064,
    serial_number: str | None = "QS1-IDU0001",
    firmware_version: str | None = "43",
    model_sku: str | None = None,
) -> IndoorUnit:
    return IndoorUnit(
        id=idu_id,
        system_id=system_id,
        space_id=space_id,
        outdoor_unit_id=outdoor_unit_id,
        hardware_id="hw-001",
        qsm_id=None,
        settings=IndoorUnitSettings(
            name="Living Room IDU",
            description="",
            light_brightness_default_percent=0.8,
            presence_fence_left_m=0.0,
            presence_fence_right_m=0.0,
            presence_fence_forward_m=0.0,
            radar_sensor_distance_from_floor_m=2.4,
        ),
        controls=IndoorUnitControls(
            fan_speed=fan_speed,
            louver_mode=louver_mode,
            louver_fixed_position=louver_fixed_position,
            led_color_code=led_color_code if led_on else 0,
            led_brightness=led_brightness,
            led_animation=LedAnimation.NONE,
            led_state=LightState.ON if led_on else LightState.OFF,
        ),
        state=IndoorUnitState(
            hvac_mode=HVACMode.HEAT,
            hvac_state=HVACState.HEAT,
            ambient_temperature_c=21.5,
            ambient_humidity_percent=45.0,
            fan_speed_rpm=800.0,
            fan_speed_setpoint_rpm=820.0,
            presence_detection_level=0.1,
            updated_at=datetime.now(tz=UTC) if online else None,
        ),
        hvac_inputs=None,
        conditions=None,
        performance_data=None,
        performance_metrics=None,
        presence=None,
        occupancy=None,
        serial_number=serial_number,
        firmware_version=firmware_version,
        model_sku=model_sku,
    )


def make_odu(
    odu_id: str = "odu-001",
    system_id: str = "sys-001",
    space_id: str = "space-001",
) -> OutdoorUnit:
    return OutdoorUnit(
        id=odu_id,
        system_id=system_id,
        space_id=space_id,
        hvac_state=2,
        model_sku="QHP-1234",
        serial_number="SN-12345",
        firmware_version="1.2.3",
        firmware_update_info_id=None,
        performance_data=OutdoorUnitPerformanceData(
            measurement_interval_s=5.0,
            energy_measurement_j=1000.0,
            compressor_frequency_hz=55.0,
            ambient_temperature_c=5.0,
            coil_temperature_c=10.0,
            exhaust_temperature_c=35.0,
            high_pressure_kpa=2500.0,
            low_pressure_kpa=800.0,
        ),
    )


def make_controller(
    ctrl_id: str = "ctrl-001",
    system_id: str = "sys-001",
    space_id: str = "space-001",
    online: bool = True,
) -> Controller:
    return Controller(
        id=ctrl_id,
        system_id=system_id,
        space_id=space_id,
        name="Quilt Dial",
        raw_thermistor_c=21.5,
        pcb_temperature_a_c=35.0,
        pcb_temperature_b_c=47.0,
        calibrated_ambient_c=22.0,
        wifi_ssid="HomeNetwork",
        wifi_ip="192.168.1.100",
        wifi_signal_dbm=-55,
        wifi_freq_mhz=5745,
        state_updated_at=datetime.now(tz=UTC) if online else None,
    )


def make_remote_sensor(
    rs_id: str = "rs-001",
    indoor_unit_id: str = "idu-001",
) -> RemoteSensor:
    return RemoteSensor(
        id=rs_id,
        indoor_unit_id=indoor_unit_id,
        mac="AA:BB:CC:DD:EE:FF",
        ambient_temperature_c=20.5,
        humidity_percent=48.0,
        battery_level_percent=85.0,
        signal_level_dbm=-65,
        control_mode=RemoteSensorControlMode.ENABLED,
    )


def make_ctrl_remote_sensor(
    crs_id: str = "crs-001",
    controller_id: str = "ctrl-001",
) -> ControllerRemoteSensor:
    return ControllerRemoteSensor(
        id=crs_id,
        controller_id=controller_id,
        mac="BB:CC:DD:EE:FF:AA",
        ambient_temperature_c=21.0,
        humidity_percent=50.0,
        battery_level_percent=90.0,
        signal_level_dbm=-60,
        control_mode=RemoteSensorControlMode.ENABLED,
    )


def make_location(
    location_id: str = "loc-001",
    name: str = "My Home",
    system_id: str = "sys-001",
    schedule_paused: bool = False,
) -> Location:
    return Location(
        id=location_id,
        name=name,
        system_id=system_id,
        timezone="America/Los_Angeles",
        schedule_paused=schedule_paused,
    )


def make_comfort_setting(
    cs_id: str = "cs-001",
    space_id: str = "space-001",
    name: str = "Cozy",
    cs_type: ComfortSettingType = ComfortSettingType.ACTIVE,
    hvac_mode: HVACMode = HVACMode.HEAT,
    heat_setpoint_c: float = 21.0,
    cool_setpoint_c: float = 25.0,
) -> ComfortSetting:
    return ComfortSetting(
        id=cs_id,
        system_id="sys-001",
        space_id=space_id,
        name=name,
        type=cs_type,
        hvac_mode=hvac_mode,
        heating_setpoint_c=heat_setpoint_c,
        cooling_setpoint_c=cool_setpoint_c,
        fan_speed=FanSpeed.AUTO,
        louver_mode=LouverMode.AUTO,
    )


def make_qsm(
    qsm_id: str = "qsm-001",
    system_id: str = "sys-001",
) -> QuiltSmartModule:
    return QuiltSmartModule(
        id=qsm_id,
        system_id=system_id,
        led_color_code=0xFF0000FF,
        sensors=QsmSensors(
            phase_detected_raw=0.5,
            target_detected_raw=0.3,
            als_illuminance_raw=200,
            als_ir_raw=50,
            als_both_raw=250,
            accel_x_raw=0,
            accel_y_raw=0,
            accel_z_raw=1000,
        ),
        hosted_wifi=None,
        ap_wifi=None,
        p2p_wifi=None,
    )


def make_snapshot(
    spaces=None,
    indoor_units=None,
    outdoor_units=None,
    controllers=None,
    quilt_smart_modules=None,
    comfort_settings=None,
    remote_sensors=None,
    controller_remote_sensors=None,
    locations=None,
) -> MagicMock:
    """Build a minimal SystemSnapshot mock with real model lists."""
    snapshot = MagicMock()
    snapshot.spaces = spaces or [make_space()]
    snapshot.indoor_units = indoor_units or [make_idu()]
    snapshot.outdoor_units = outdoor_units or [make_odu()]
    snapshot.controllers = controllers or []
    snapshot.quilt_smart_modules = quilt_smart_modules or []
    snapshot.comfort_settings = comfort_settings or []
    snapshot.schedule_weeks = []
    snapshot.schedule_days = []
    snapshot.remote_sensors = remote_sensors or []
    snapshot.controller_remote_sensors = controller_remote_sensors or []
    snapshot.software_update_infos = []
    snapshot.locations = locations or [make_location()]
    snapshot.stream_topics.return_value = ["topic-1"]
    return snapshot


# ── Coordinator / client mocks ────────────────────────────────────────────────


def make_mock_coordinator(hass: HomeAssistant, snapshot=None) -> MagicMock:
    """Return a pre-configured mock coordinator."""
    from custom_components.quilt_hp.coordinator import QuiltCoordinator

    coordinator = MagicMock(spec=QuiltCoordinator)
    coordinator.hass = hass
    data = snapshot or make_snapshot()
    coordinator.data = data
    coordinator.spaces_by_id = {s.id: s for s in data.spaces}
    coordinator.idu_by_id = {u.id: u for u in data.indoor_units}
    coordinator.idu_by_space_id = {
        u.space_id: u for u in data.indoor_units if u.space_id
    }
    first_idu: dict[str, str] = {}
    for idu in data.indoor_units:
        if idu.space_id and idu.space_id not in first_idu:
            first_idu[idu.space_id] = idu.id
    coordinator.first_idu_id_by_space_id = first_idu
    coordinator.odu_by_id = {u.id: u for u in data.outdoor_units}
    coordinator.ctrl_by_id = {c.id: c for c in data.controllers}
    coordinator.qsm_by_id = {q.id: q for q in data.quilt_smart_modules}
    coordinator.cs_by_id = {cs.id: cs for cs in data.comfort_settings}
    cs_by_space: dict[str, list] = {}
    for cs in data.comfort_settings:
        cs_by_space.setdefault(cs.space_id, []).append(cs)
    coordinator.cs_by_space_id = cs_by_space
    coordinator.remote_sensor_by_id = {r.id: r for r in data.remote_sensors}
    coordinator.ctrl_remote_sensor_by_id = {
        r.id: r for r in data.controller_remote_sensors
    }
    coordinator.location_by_id = {loc.id: loc for loc in data.locations}
    coordinator.energy_by_space_id = {}
    coordinator.energy_last_reset = None
    coordinator.last_update_success = True
    coordinator.stream_death_count = 0
    coordinator.client = MagicMock()
    coordinator.async_set_space = AsyncMock()
    coordinator.async_set_indoor_unit = AsyncMock()
    coordinator.async_set_schedule_execution = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    coordinator.is_streaming = False
    # Capture update listeners so tests can simulate coordinator pushes.
    coordinator.listeners = []

    def _add_listener(callback, context=None):
        coordinator.listeners.append(callback)
        return lambda: coordinator.listeners.remove(callback)

    coordinator.async_add_listener = MagicMock(side_effect=_add_listener)
    return coordinator


def make_entry_mock(hass: HomeAssistant | None = None) -> MagicMock:
    """Return a minimal ConfigEntry mock for constructing a QuiltCoordinator.

    Background tasks created via ``async_create_background_task`` are recorded
    in ``entry.created_task_names``. When *hass* is given, the task coroutine
    is actually scheduled on the event loop; otherwise it is closed unawaited.
    """
    entry = MagicMock()
    entry.options = {}
    entry.created_task_names = []

    def _bg_task(_hass, coro, name=None, eager_start=True):
        entry.created_task_names.append(name)
        if hass is not None:
            return hass.async_create_task(coro)
        coro.close()
        task = MagicMock()
        task.done.return_value = False
        return task

    entry.async_create_background_task = MagicMock(side_effect=_bg_task)
    return entry


def get_stream_callback(stream: MagicMock, name: str):
    """Return the callback the coordinator registered on the mock stream."""
    return getattr(stream, name).call_args[0][0]


@pytest.fixture
def mock_client():
    """Patch QuiltClient inside the coordinator module.

    Yields ``(client, stream)``. The stream mock tolerates all ``on_*``
    registration methods (plain MagicMock attributes) and provides awaitable
    ``start()``/``stop()`` plus ``is_connected``/``stream_state``.
    """
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.login = AsyncMock()
    client.get_snapshot = AsyncMock(return_value=make_snapshot())
    client.invalidate_snapshot = MagicMock()
    client.get_energy = AsyncMock(return_value=[])
    client.set_space = AsyncMock()
    client.set_indoor_unit = AsyncMock()
    client.set_schedule_execution = AsyncMock()
    client.list_systems = AsyncMock(return_value=[])

    stream = MagicMock()
    stream.start = AsyncMock()
    stream.stop = AsyncMock()
    stream.is_connected = True
    stream.stream_state = "connected"
    client.stream.return_value = stream

    with (
        patch(
            "custom_components.quilt_hp.coordinator.QuiltClient", return_value=client
        ),
        patch("custom_components.quilt_hp.coordinator.HATokenStore"),
    ):
        yield client, stream
