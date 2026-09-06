"""Tests for the sensor platform."""

from __future__ import annotations

from datetime import UTC, datetime
import math
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from quilt_hp.models.enums import ComfortSettingType, HVACState

from custom_components.quilt_hp.sensor import (
    CONTROLLER_REMOTE_SENSOR_DESCRIPTIONS,
    CONTROLLER_SENSOR_DESCRIPTIONS,
    IDU_SENSOR_DESCRIPTIONS,
    ODU_SENSOR_DESCRIPTIONS,
    QSM_SENSOR_DESCRIPTIONS,
    REMOTE_SENSOR_DESCRIPTIONS,
    SPACE_SENSOR_DESCRIPTIONS,
    QuiltComfortSettingSensor,
    QuiltControllerRemoteSensor,
    QuiltControllerSensor,
    QuiltEnergySensor,
    QuiltIDUSensor,
    QuiltODUSensor,
    QuiltQSMSensor,
    QuiltRemoteSensor,
    QuiltSpaceSensor,
    async_setup_entry,
)

from .conftest import (
    make_comfort_setting,
    make_controller,
    make_ctrl_remote_sensor,
    make_idu,
    make_mock_coordinator,
    make_odu,
    make_qsm,
    make_remote_sensor,
    make_snapshot,
    make_space,
    register_device,
)


class FalsyHealthStatus:
    """Falsy stand-in for a health status enum value."""

    def __init__(self, name: str) -> None:
        """Store the enum-like name."""
        self.name = name

    def __bool__(self) -> bool:
        """Behave like an IntEnum zero value."""
        return False


@pytest.fixture
def coordinator(hass):
    space = make_space(ambient_temp_c=21.5)
    idu = make_idu()
    odu = make_odu()
    snapshot = make_snapshot(spaces=[space], indoor_units=[idu], outdoor_units=[odu])
    return make_mock_coordinator(hass, snapshot)


@pytest.fixture
def coordinator_with_ctrl(hass):
    space = make_space()
    idu = make_idu()
    odu = make_odu()
    ctrl = make_controller()
    snapshot = make_snapshot(
        spaces=[space], indoor_units=[idu], outdoor_units=[odu], controllers=[ctrl]
    )
    return make_mock_coordinator(hass, snapshot)


def test_space_ambient_temperature(coordinator) -> None:
    desc = next(d for d in SPACE_SENSOR_DESCRIPTIONS if d.key == "space_temperature")
    entity = QuiltSpaceSensor(coordinator, "space-001", "idu-001", desc)
    assert entity.native_value == 21.5


def test_space_ambient_temperature_nan_is_none(hass) -> None:
    space = make_space(ambient_temp_c=21.5)
    space.state.ambient_temperature_c = math.nan
    coordinator = make_mock_coordinator(hass, make_snapshot(spaces=[space]))
    desc = next(d for d in SPACE_SENSOR_DESCRIPTIONS if d.key == "space_temperature")
    entity = QuiltSpaceSensor(coordinator, "space-001", "idu-001", desc)
    assert entity.native_value is None


def test_active_comfort_setting_reflects_type(hass) -> None:
    space = make_space()  # controls.comfort_setting_id defaults to "cs-001"
    cs = make_comfort_setting(
        cs_id="cs-001", name="Cozy", cs_type=ComfortSettingType.SLEEP
    )
    coordinator = make_mock_coordinator(
        hass, make_snapshot(spaces=[space], comfort_settings=[cs])
    )
    entity = QuiltComfortSettingSensor(coordinator, "space-001", "idu-001")
    assert entity.native_value == "sleep"
    assert entity.extra_state_attributes == {"comfort_setting_name": "Cozy"}


def test_active_comfort_setting_none_when_unlinked(hass) -> None:
    space = make_space()
    space.controls.comfort_setting_id = ""
    coordinator = make_mock_coordinator(hass, make_snapshot(spaces=[space]))
    entity = QuiltComfortSettingSensor(coordinator, "space-001", "idu-001")
    assert entity.native_value is None
    assert entity.extra_state_attributes is None


def test_active_comfort_setting_none_when_unspecified(hass) -> None:
    space = make_space()
    cs = make_comfort_setting(cs_id="cs-001", cs_type=ComfortSettingType.UNSPECIFIED)
    coordinator = make_mock_coordinator(
        hass, make_snapshot(spaces=[space], comfort_settings=[cs])
    )
    entity = QuiltComfortSettingSensor(coordinator, "space-001", "idu-001")
    assert entity.native_value is None


def test_idu_ambient_temperature(coordinator) -> None:
    desc = next(d for d in IDU_SENSOR_DESCRIPTIONS if d.key == "ambient_temperature")
    entity = QuiltIDUSensor(coordinator, "idu-001", desc)
    assert entity.native_value == 21.5


def test_idu_humidity(coordinator) -> None:
    desc = next(d for d in IDU_SENSOR_DESCRIPTIONS if d.key == "ambient_humidity")
    entity = QuiltIDUSensor(coordinator, "idu-001", desc)
    assert entity.native_value == 45.0


def test_idu_coil_temperature_none_when_no_perf_data(coordinator) -> None:
    desc = next(d for d in IDU_SENSOR_DESCRIPTIONS if d.key == "coil_temperature")
    entity = QuiltIDUSensor(coordinator, "idu-001", desc)
    # make_idu sets performance_data=None
    assert entity.native_value is None


def test_idu_coil_temperature_with_perf_data(hass) -> None:
    from quilt_hp.models.indoor_unit import IndoorUnitPerformanceData

    idu = make_idu()
    idu.performance_data = IndoorUnitPerformanceData(  # type: ignore[misc]
        measurement_interval_s=10.0,
        energy_measurement_j=500.0,
        hvac_mode=idu.state.hvac_mode,
        hvac_state=idu.state.hvac_state,
        actual_fan_speed_rpm=800.0,
        outlet_temperature_c=30.0,
        inlet_temperature_c=20.0,
        inlet_humidity_pct=45.0,
        coil_temperature_c=12.5,
        gas_pipe_temperature_c=8.0,
        liquid_pipe_temperature_c=35.0,
    )
    coordinator = make_mock_coordinator(hass, make_snapshot(indoor_units=[idu]))
    desc = next(d for d in IDU_SENSOR_DESCRIPTIONS if d.key == "coil_temperature")
    entity = QuiltIDUSensor(coordinator, "idu-001", desc)
    assert entity.native_value == 12.5


def test_idu_module_power(hass) -> None:
    from quilt_hp.models.indoor_unit import IndoorUnitPerformanceData

    idu = make_idu()
    idu.performance_data = IndoorUnitPerformanceData(  # type: ignore[misc]
        measurement_interval_s=10.0,
        energy_measurement_j=500.0,
        hvac_mode=idu.state.hvac_mode,
        hvac_state=idu.state.hvac_state,
        actual_fan_speed_rpm=800.0,
        outlet_temperature_c=30.0,
        inlet_temperature_c=20.0,
        inlet_humidity_pct=45.0,
        coil_temperature_c=12.5,
        gas_pipe_temperature_c=8.0,
        liquid_pipe_temperature_c=35.0,
    )
    coordinator = make_mock_coordinator(hass, make_snapshot(indoor_units=[idu]))
    desc = next(d for d in IDU_SENSOR_DESCRIPTIONS if d.key == "module_power")
    entity = QuiltIDUSensor(coordinator, "idu-001", desc)
    assert entity.native_value == 50.0  # 500J / 10s


def test_idu_led_power_none_when_no_metrics(coordinator) -> None:
    desc = next(d for d in IDU_SENSOR_DESCRIPTIONS if d.key == "led_power")
    entity = QuiltIDUSensor(coordinator, "idu-001", desc)
    assert entity.native_value is None


def test_odu_ambient_temperature(coordinator) -> None:
    desc = next(d for d in ODU_SENSOR_DESCRIPTIONS if d.key == "ambient_temperature")
    entity = QuiltODUSensor(coordinator, "odu-001", "idu-001", desc)
    assert entity.native_value == 5.0


def test_odu_compressor_frequency(coordinator) -> None:
    desc = next(d for d in ODU_SENSOR_DESCRIPTIONS if d.key == "compressor_frequency")
    entity = QuiltODUSensor(coordinator, "odu-001", "idu-001", desc)
    assert entity.native_value == 55.0


def test_odu_coil_temperature(coordinator) -> None:
    desc = next(d for d in ODU_SENSOR_DESCRIPTIONS if d.key == "coil_temperature")
    entity = QuiltODUSensor(coordinator, "odu-001", "idu-001", desc)
    assert entity.native_value == 10.0


def test_odu_exhaust_temperature(coordinator) -> None:
    desc = next(d for d in ODU_SENSOR_DESCRIPTIONS if d.key == "exhaust_temperature")
    entity = QuiltODUSensor(coordinator, "odu-001", "idu-001", desc)
    assert entity.native_value == 35.0


def test_controller_ambient_temperature(coordinator_with_ctrl) -> None:
    desc = next(
        d for d in CONTROLLER_SENSOR_DESCRIPTIONS if d.key == "ambient_temperature"
    )
    entity = QuiltControllerSensor(coordinator_with_ctrl, "ctrl-001", desc)
    # ambient_temperature_c is a @property on Controller derived from raw_thermistor_c
    assert entity.native_value is not None


def test_controller_pcb_temperature_a(coordinator_with_ctrl) -> None:
    desc = next(
        d for d in CONTROLLER_SENSOR_DESCRIPTIONS if d.key == "pcb_temperature_a"
    )
    entity = QuiltControllerSensor(coordinator_with_ctrl, "ctrl-001", desc)
    assert entity.native_value == 35.0


def test_controller_pcb_temperature_b(coordinator_with_ctrl) -> None:
    desc = next(
        d for d in CONTROLLER_SENSOR_DESCRIPTIONS if d.key == "pcb_temperature_b"
    )
    entity = QuiltControllerSensor(coordinator_with_ctrl, "ctrl-001", desc)
    assert entity.native_value == 47.0


def test_controller_calibrated_ambient(coordinator_with_ctrl) -> None:
    desc = next(
        d
        for d in CONTROLLER_SENSOR_DESCRIPTIONS
        if d.key == "calibrated_ambient_temperature"
    )
    entity = QuiltControllerSensor(coordinator_with_ctrl, "ctrl-001", desc)
    assert entity.native_value == 22.0


def test_controller_wifi_signal(coordinator_with_ctrl) -> None:
    desc = next(d for d in CONTROLLER_SENSOR_DESCRIPTIONS if d.key == "wifi_signal")
    entity = QuiltControllerSensor(coordinator_with_ctrl, "ctrl-001", desc)
    assert entity.native_value == -55


def test_controller_wifi_frequency(coordinator_with_ctrl) -> None:
    desc = next(d for d in CONTROLLER_SENSOR_DESCRIPTIONS if d.key == "wifi_frequency")
    entity = QuiltControllerSensor(coordinator_with_ctrl, "ctrl-001", desc)
    assert entity.native_value == 5745


def test_qsm_local_comms_health_unspecified(hass) -> None:
    idu = make_idu()
    idu.qsm_id = "qsm-001"
    snapshot = make_snapshot(indoor_units=[idu])
    coordinator = make_mock_coordinator(hass, snapshot)
    coordinator.qsm_by_id = {
        "qsm-001": SimpleNamespace(
            local_comms_health=FalsyHealthStatus("UNSPECIFIED"), sensors=None
        )
    }
    desc = next(d for d in QSM_SENSOR_DESCRIPTIONS if d.key == "local_comms_health")
    entity = QuiltQSMSensor(coordinator, "idu-001", desc)
    assert entity.native_value == "unspecified"


def test_controller_local_comms_health_unspecified(coordinator_with_ctrl) -> None:
    ctrl = coordinator_with_ctrl.ctrl_by_id["ctrl-001"]
    ctrl.local_comms_health = FalsyHealthStatus("UNSPECIFIED")
    desc = next(
        d for d in CONTROLLER_SENSOR_DESCRIPTIONS if d.key == "local_comms_health"
    )
    entity = QuiltControllerSensor(coordinator_with_ctrl, "ctrl-001", desc)
    assert entity.native_value == "unspecified"


def test_remote_sensor_temperature(hass) -> None:
    rs = make_remote_sensor()
    snapshot = make_snapshot(remote_sensors=[rs])
    coordinator = make_mock_coordinator(hass, snapshot)
    desc = next(d for d in REMOTE_SENSOR_DESCRIPTIONS if d.key == "temperature")
    entity = QuiltRemoteSensor(coordinator, "rs-001", desc)
    assert entity.native_value == 20.5


def test_remote_sensor_humidity(hass) -> None:
    rs = make_remote_sensor()
    snapshot = make_snapshot(remote_sensors=[rs])
    coordinator = make_mock_coordinator(hass, snapshot)
    desc = next(d for d in REMOTE_SENSOR_DESCRIPTIONS if d.key == "humidity")
    entity = QuiltRemoteSensor(coordinator, "rs-001", desc)
    assert entity.native_value == 48.0


def test_remote_sensor_battery(hass) -> None:
    rs = make_remote_sensor()
    snapshot = make_snapshot(remote_sensors=[rs])
    coordinator = make_mock_coordinator(hass, snapshot)
    desc = next(d for d in REMOTE_SENSOR_DESCRIPTIONS if d.key == "battery")
    entity = QuiltRemoteSensor(coordinator, "rs-001", desc)
    assert entity.native_value == 85.0


def test_ctrl_remote_sensor_temperature(hass) -> None:
    crs = make_ctrl_remote_sensor()
    snapshot = make_snapshot(controller_remote_sensors=[crs])
    coordinator = make_mock_coordinator(hass, snapshot)
    desc = next(
        d for d in CONTROLLER_REMOTE_SENSOR_DESCRIPTIONS if d.key == "temperature"
    )
    entity = QuiltControllerRemoteSensor(coordinator, "crs-001", desc)
    assert entity.native_value == 21.0


def test_ctrl_remote_sensor_battery(hass) -> None:
    crs = make_ctrl_remote_sensor()
    snapshot = make_snapshot(controller_remote_sensors=[crs])
    coordinator = make_mock_coordinator(hass, snapshot)
    desc = next(d for d in CONTROLLER_REMOTE_SENSOR_DESCRIPTIONS if d.key == "battery")
    entity = QuiltControllerRemoteSensor(coordinator, "crs-001", desc)
    assert entity.native_value == 90.0


# ── Parent device links ───────────────────────────────────────────────────────


def test_odu_sensor_links_to_idu(hass, coordinator) -> None:
    idu_device_id = register_device(hass, coordinator.config_entry, "i_idu-001")
    desc = next(d for d in ODU_SENSOR_DESCRIPTIONS if d.key == "ambient_temperature")
    entity = QuiltODUSensor(coordinator, "odu-001", "idu-001", desc)
    assert entity.device_info["via_device_id"] == idu_device_id


def test_odu_sensor_no_link_when_idu_unregistered(hass, coordinator) -> None:
    desc = next(d for d in ODU_SENSOR_DESCRIPTIONS if d.key == "ambient_temperature")
    entity = QuiltODUSensor(coordinator, "odu-001", "idu-001", desc)
    assert "via_device_id" not in entity.device_info


def test_remote_sensor_links_to_idu(hass) -> None:
    coordinator = make_mock_coordinator(
        hass, make_snapshot(remote_sensors=[make_remote_sensor()])
    )
    idu_device_id = register_device(hass, coordinator.config_entry, "i_idu-001")
    desc = next(d for d in REMOTE_SENSOR_DESCRIPTIONS if d.key == "temperature")
    entity = QuiltRemoteSensor(coordinator, "rs-001", desc)
    assert entity.device_info["via_device_id"] == idu_device_id


def test_ctrl_remote_sensor_links_to_controller(hass) -> None:
    coordinator = make_mock_coordinator(
        hass,
        make_snapshot(
            controllers=[make_controller()],
            controller_remote_sensors=[make_ctrl_remote_sensor()],
        ),
    )
    ctrl_device_id = register_device(hass, coordinator.config_entry, "c_ctrl-001")
    desc = next(
        d for d in CONTROLLER_REMOTE_SENSOR_DESCRIPTIONS if d.key == "temperature"
    )
    entity = QuiltControllerRemoteSensor(coordinator, "crs-001", desc)
    assert entity.device_info["via_device_id"] == ctrl_device_id


def test_energy_sensor_returns_none_before_first_fetch(coordinator) -> None:
    entity = QuiltEnergySensor(coordinator, "space-001", "idu-001")
    assert entity.native_value is None
    assert entity.last_reset is None


def test_energy_sensor_returns_value_after_fetch(hass) -> None:
    coordinator = make_mock_coordinator(hass)
    coordinator.energy_by_space_id = {"space-001": 3.14159}
    coordinator.energy_last_reset = datetime(2026, 5, 12, 0, 0, 0, tzinfo=UTC)
    entity = QuiltEnergySensor(coordinator, "space-001", "idu-001")
    assert entity.native_value == 3.1416
    assert entity.last_reset == datetime(2026, 5, 12, 0, 0, 0, tzinfo=UTC)


def test_energy_sensor_missing_space_returns_none(hass) -> None:
    coordinator = make_mock_coordinator(hass)
    coordinator.energy_by_space_id = {"other-space": 1.0}
    entity = QuiltEnergySensor(coordinator, "space-001", "idu-001")
    assert entity.native_value is None


def test_idu_unavailable_when_offline(hass) -> None:
    idu = make_idu(online=False)
    coordinator = make_mock_coordinator(hass, make_snapshot(indoor_units=[idu]))
    desc = next(d for d in IDU_SENSOR_DESCRIPTIONS if d.key == "ambient_temperature")
    entity = QuiltIDUSensor(coordinator, "idu-001", desc)
    assert not entity.available


def test_idu_inlet_temperature_keeps_zero_value(hass) -> None:
    idu = make_idu()
    idu.state.inlet_temperature_c = 0.0
    coordinator = make_mock_coordinator(hass, make_snapshot(indoor_units=[idu]))
    desc = next(d for d in IDU_SENSOR_DESCRIPTIONS if d.key == "inlet_temperature")
    entity = QuiltIDUSensor(coordinator, "idu-001", desc)
    assert entity.native_value == 0.0


def test_odu_unique_id_excludes_idu_id() -> None:
    """ODU unique ID must not include an IDU ID — the ODU is a standalone device."""
    desc = next(d for d in ODU_SENSOR_DESCRIPTIONS if d.key == "ambient_temperature")
    # Simulate two different IDUs both referencing the same ODU
    coordinator = MagicMock()
    entity_a = QuiltODUSensor(coordinator, "odu-001", "idu-001", desc)
    entity_b = QuiltODUSensor(coordinator, "odu-001", "idu-002", desc)
    assert entity_a.unique_id == entity_b.unique_id
    assert "idu" not in (entity_a.unique_id or "")


async def test_shared_odu_creates_one_sensor_set(hass) -> None:
    """When two IDUs share an ODU, only one set of ODU sensors should be created."""
    idu1 = make_idu(idu_id="idu-001", space_id="space-001", outdoor_unit_id="odu-001")
    idu2 = make_idu(idu_id="idu-002", space_id="space-002", outdoor_unit_id="odu-001")
    odu = make_odu(odu_id="odu-001")
    snapshot = make_snapshot(indoor_units=[idu1, idu2], outdoor_units=[odu])
    coordinator = make_mock_coordinator(hass, snapshot)

    created: list[QuiltODUSensor] = []

    def capture(entities, **_kwargs):
        created.extend(e for e in entities if isinstance(e, QuiltODUSensor))

    entry = MagicMock()
    entry.entry_id = "test"
    entry.runtime_data = coordinator

    await async_setup_entry(hass, entry, capture)

    odu_unique_ids = {e.unique_id for e in created}
    assert len(odu_unique_ids) == len(ODU_SENSOR_DESCRIPTIONS), (
        "Expected one sensor per ODU description, got duplicates"
    )


async def test_dynamic_new_idu_adds_entities(hass) -> None:
    """A new IDU appearing in coordinator data must be added on the fly."""
    snapshot = make_snapshot()
    coordinator = make_mock_coordinator(hass, snapshot)

    batches: list[list] = []

    def capture(entities, **_kwargs):
        batches.append(list(entities))

    entry = MagicMock()
    entry.entry_id = "test"
    entry.runtime_data = coordinator

    await async_setup_entry(hass, entry, capture)
    assert len(batches) == 1
    # Listener must be registered for future coordinator updates.
    coordinator.async_add_listener.assert_called_once()
    entry.async_on_unload.assert_called_once()

    # Simulate a new IDU (and its space) appearing in a later snapshot.
    new_space = make_space(space_id="space-002")
    new_idu = make_idu(idu_id="idu-002", space_id="space-002")
    snapshot.spaces.append(new_space)
    snapshot.indoor_units.append(new_idu)
    coordinator.spaces_by_id[new_space.id] = new_space
    coordinator.idu_by_id[new_idu.id] = new_idu
    coordinator.first_idu_id_by_space_id[new_space.id] = new_idu.id

    for listener in coordinator.listeners:
        listener()

    assert len(batches) == 2
    new_unique_ids = {e.unique_id for e in batches[1]}
    assert any("idu-002" in (uid or "") for uid in new_unique_ids)
    # The pre-existing IDU must not be re-added.
    assert not any("idu-001" in (uid or "") for uid in new_unique_ids)


async def test_dynamic_no_duplicate_add_on_unchanged_data(hass) -> None:
    """Coordinator updates without new devices must not re-add entities."""
    coordinator = make_mock_coordinator(hass, make_snapshot())

    batches: list[list] = []

    def capture(entities, **_kwargs):
        batches.append(list(entities))

    entry = MagicMock()
    entry.entry_id = "test"
    entry.runtime_data = coordinator

    await async_setup_entry(hass, entry, capture)
    for listener in coordinator.listeners:
        listener()

    assert len(batches) == 1


def test_qsm_sensor_values(hass) -> None:
    idu = make_idu()
    idu.qsm_id = "qsm-001"
    qsm = make_qsm()
    snapshot = make_snapshot(indoor_units=[idu], quilt_smart_modules=[qsm])
    coordinator = make_mock_coordinator(hass, snapshot)

    values = {}
    for desc in QSM_SENSOR_DESCRIPTIONS:
        entity = QuiltQSMSensor(coordinator, "idu-001", desc)
        values[desc.key] = entity.native_value

    assert values["phase_detected_raw"] == 0.5
    assert values["target_detected_raw"] == 0.3
    assert values["als_illuminance"] == 200


def test_qsm_sensor_unavailable_without_qsm(coordinator) -> None:
    """IDU without a paired QSM: QSM sensors must be unavailable."""
    desc = next(d for d in QSM_SENSOR_DESCRIPTIONS if d.key == "phase_detected_raw")
    entity = QuiltQSMSensor(coordinator, "idu-001", desc)
    assert not entity.available
    assert entity.native_value is None


def _idu_with_metrics(hass, **overrides):
    from quilt_hp.models.indoor_unit import IndoorUnitPerformanceMetrics

    idu = make_idu()
    defaults = {
        "capacity_w": 0.0,
        "coefficient_of_performance": 0.0,
        "hvac_power_w": 2.26,
        "led_power_w": 0.0,
        "hvac_mode": idu.state.hvac_mode,
        "hvac_state": idu.state.hvac_state,
    }
    defaults.update(overrides)
    idu.performance_metrics = IndoorUnitPerformanceMetrics(**defaults)  # type: ignore[misc]
    return make_mock_coordinator(hass, make_snapshot(indoor_units=[idu]))


def test_idu_cop_zero_when_idle_is_unknown(hass) -> None:
    """The wire reports COP 0 when not actively heating/cooling → unknown."""
    coordinator = _idu_with_metrics(
        hass, coefficient_of_performance=0.0, hvac_state=HVACState.STANDBY
    )
    desc = next(
        d for d in IDU_SENSOR_DESCRIPTIONS if d.key == "coefficient_of_performance"
    )
    entity = QuiltIDUSensor(coordinator, "idu-001", desc)
    assert entity.native_value is None


def test_idu_cop_reported_when_running(hass) -> None:
    coordinator = _idu_with_metrics(hass, coefficient_of_performance=3.517)
    desc = next(
        d for d in IDU_SENSOR_DESCRIPTIONS if d.key == "coefficient_of_performance"
    )
    entity = QuiltIDUSensor(coordinator, "idu-001", desc)
    assert entity.native_value == 3.52


def test_idu_capacity_zero_when_idle_is_unknown(hass) -> None:
    coordinator = _idu_with_metrics(hass, capacity_w=0.0, hvac_state=HVACState.STANDBY)
    desc = next(d for d in IDU_SENSOR_DESCRIPTIONS if d.key == "hvac_capacity")
    entity = QuiltIDUSensor(coordinator, "idu-001", desc)
    assert entity.native_value is None


def test_idu_capacity_reported_when_running(hass) -> None:
    coordinator = _idu_with_metrics(hass, capacity_w=2800.0)
    desc = next(d for d in IDU_SENSOR_DESCRIPTIONS if d.key == "hvac_capacity")
    entity = QuiltIDUSensor(coordinator, "idu-001", desc)
    assert entity.native_value == 2800.0
