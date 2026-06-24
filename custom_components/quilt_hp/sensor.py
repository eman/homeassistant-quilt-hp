"""Sensor platform for Quilt Heat Pump.

Provides sensor entities for:
- QSM/IDU: space temperature (space-calibrated), unit temp, humidity,
           fan RPM, inlet/outlet temp, presence level,
           COP, HVAC capacity (W), HVAC power (W), LED power (W),
           coil/gas-pipe/liquid-pipe temperatures, inlet humidity,
           module power, calibrated ambient temp, radar signals, illuminance
- OutdoorUnit: ambient temp, coil temp, exhaust temp, compressor frequency,
               pressures
- Controller (Dial): ambient temperature, PCB temps, calibrated ambient,
                     WiFi signal, WiFi frequency
- RemoteSensor (IDU-paired): temperature, humidity, battery, signal
- ControllerRemoteSensor (Dial-paired): temperature, humidity, battery, signal
- Space energy: today's kWh per room (from the energy API)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast, override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    LIGHT_LUX,
    PERCENTAGE,
    REVOLUTIONS_PER_MINUTE,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfPressure,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from quilt_hp.models.controller import Controller
from quilt_hp.models.indoor_unit import IndoorUnit
from quilt_hp.models.outdoor_unit import OutdoorUnit
from quilt_hp.models.qsm import QuiltSmartModule
from quilt_hp.models.sensor import ControllerRemoteSensor, RemoteSensor
from quilt_hp.models.space import Space

from .coordinator import QuiltCoordinator
from .entity import (
    QuiltEntity,
    controller_device_info,
    ctrl_remote_sensor_device_info,
    idu_device_info,
    odu_device_info,
    remote_sensor_device_info,
)
from .utils import normalize_temperature as _normalize_temperature

if TYPE_CHECKING:
    from . import QuiltConfigEntry


def _local_comms_health_name(health: Any | None) -> str | None:
    """Return the health enum name, preserving falsy enum values."""
    if health is None:
        return None
    return cast(str, health.name)


# ── Space temperature sensor (on QSM device) ──────────────────────────────────


@dataclass(frozen=True, kw_only=True)
class SpaceSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Space], Any] = lambda _: None


SPACE_SENSOR_DESCRIPTIONS: tuple[SpaceSensorDescription, ...] = (
    SpaceSensorDescription(
        key="space_temperature",
        name="Space Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda space: (
            None
            if getattr(space.state, "has_missing_ambient_temperature", False)
            else _normalize_temperature(space.state.ambient_temperature_c)
        ),
    ),
)


# ── IndoorUnit sensors ────────────────────────────────────────────────────────


@dataclass(frozen=True, kw_only=True)
class IDUSensorDescription(SensorEntityDescription):
    value_fn: Callable[[IndoorUnit], Any] = lambda _: None
    available_fn: Callable[[IndoorUnit], bool] = lambda idu: idu.is_online


IDU_SENSOR_DESCRIPTIONS: tuple[IDUSensorDescription, ...] = (
    IDUSensorDescription(
        key="ambient_temperature",
        name="Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda idu: _normalize_temperature(idu.state.ambient_temperature_c),
    ),
    IDUSensorDescription(
        key="ambient_humidity",
        name="Humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda idu: idu.state.ambient_humidity_percent,
    ),
    IDUSensorDescription(
        key="fan_speed_rpm",
        name="Fan Speed",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: idu.state.fan_speed_rpm,
    ),
    IDUSensorDescription(
        key="fan_speed_setpoint_rpm",
        name="Fan Speed Setpoint",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: idu.state.fan_speed_setpoint_rpm,
        entity_registry_enabled_default=False,
    ),
    IDUSensorDescription(
        key="inlet_temperature",
        name="Inlet Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: _normalize_temperature(idu.state.inlet_temperature_c),
        entity_registry_enabled_default=False,
    ),
    IDUSensorDescription(
        key="outlet_temperature",
        name="Outlet Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: _normalize_temperature(idu.state.outlet_temperature_c),
        entity_registry_enabled_default=False,
    ),
    IDUSensorDescription(
        key="presence_level",
        name="Presence Level",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: round(idu.state.presence_detection_level * 100, 1),
        entity_registry_enabled_default=False,
    ),
    IDUSensorDescription(
        key="hvac_capacity",
        name="HVAC Capacity",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: (
            idu.performance_metrics.capacity_w if idu.performance_metrics else None
        ),
        entity_registry_enabled_default=False,
    ),
    IDUSensorDescription(
        key="hvac_power",
        name="HVAC Power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: (
            idu.performance_metrics.hvac_power_w if idu.performance_metrics else None
        ),
        entity_registry_enabled_default=False,
    ),
    IDUSensorDescription(
        key="led_power",
        name="LED Power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: (
            idu.performance_metrics.led_power_w if idu.performance_metrics else None
        ),
        entity_registry_enabled_default=False,
    ),
    IDUSensorDescription(
        key="coefficient_of_performance",
        name="COP",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: (
            round(idu.performance_metrics.coefficient_of_performance, 2)
            if idu.performance_metrics
            else None
        ),
        entity_registry_enabled_default=False,
    ),
    IDUSensorDescription(
        key="calculated_ambient_temperature",
        name="Calibrated Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: _normalize_temperature(
            idu.state.calculated_ambient_temperature_c
        ),
        entity_registry_enabled_default=False,
    ),
    # Performance data sensors (detailed refrigerant / heat-exchanger telemetry)
    IDUSensorDescription(
        key="coil_temperature",
        name="Coil Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: (
            _normalize_temperature(idu.performance_data.coil_temperature_c)
            if idu.performance_data
            else None
        ),
        entity_registry_enabled_default=False,
    ),
    IDUSensorDescription(
        key="gas_pipe_temperature",
        name="Gas Pipe Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: (
            _normalize_temperature(idu.performance_data.gas_pipe_temperature_c)
            if idu.performance_data
            else None
        ),
        entity_registry_enabled_default=False,
    ),
    IDUSensorDescription(
        key="liquid_pipe_temperature",
        name="Liquid Pipe Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: (
            _normalize_temperature(idu.performance_data.liquid_pipe_temperature_c)
            if idu.performance_data
            else None
        ),
        entity_registry_enabled_default=False,
    ),
    IDUSensorDescription(
        key="inlet_humidity_perf",
        name="Inlet Humidity (Perf)",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: (
            idu.performance_data.inlet_humidity_pct if idu.performance_data else None
        ),
        entity_registry_enabled_default=False,
    ),
    IDUSensorDescription(
        key="module_power",
        name="Module Power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: (
            round(
                idu.performance_data.energy_measurement_j
                / idu.performance_data.measurement_interval_s,
                2,
            )
            if idu.performance_data
            and idu.performance_data.measurement_interval_s
            and idu.performance_data.measurement_interval_s > 0
            else None
        ),
        entity_registry_enabled_default=False,
    ),
)


# ── QSM (radar / ALS) sensors — on IDU device ────────────────────────────────


@dataclass(frozen=True, kw_only=True)
class QSMSensorDescription(SensorEntityDescription):
    value_fn: Callable[[QuiltSmartModule], Any] = lambda _: None


QSM_SENSOR_DESCRIPTIONS: tuple[QSMSensorDescription, ...] = (
    QSMSensorDescription(
        key="phase_detected_raw",
        name="Motion Signal",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda qsm: qsm.sensors.phase_detected_raw if qsm.sensors else None,
        entity_registry_enabled_default=False,
    ),
    QSMSensorDescription(
        key="target_detected_raw",
        name="Presence Signal",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda qsm: qsm.sensors.target_detected_raw if qsm.sensors else None,
        entity_registry_enabled_default=False,
    ),
    QSMSensorDescription(
        key="als_illuminance",
        name="Illuminance",
        device_class=SensorDeviceClass.ILLUMINANCE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=LIGHT_LUX,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda qsm: qsm.sensors.als_illuminance_raw if qsm.sensors else None,
        entity_registry_enabled_default=False,
    ),
    QSMSensorDescription(
        key="local_comms_health",
        name="Local Comms Health",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda qsm: _local_comms_health_name(qsm.local_comms_health),
        entity_registry_enabled_default=False,
    ),
)


# ── OutdoorUnit sensors ───────────────────────────────────────────────────────


@dataclass(frozen=True, kw_only=True)
class ODUSensorDescription(SensorEntityDescription):
    value_fn: Callable[[OutdoorUnit], Any] = lambda _: None
    available_fn: Callable[[OutdoorUnit], bool] = lambda odu: (
        odu.performance_data is not None
    )


ODU_SENSOR_DESCRIPTIONS: tuple[ODUSensorDescription, ...] = (
    ODUSensorDescription(
        key="ambient_temperature",
        name="Outdoor Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        # Primary sensor - no category
        value_fn=lambda odu: (
            _normalize_temperature(odu.performance_data.ambient_temperature_c)
            if odu.performance_data
            else None
        ),
    ),
    ODUSensorDescription(
        key="coil_temperature",
        name="Coil Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda odu: (
            _normalize_temperature(odu.performance_data.coil_temperature_c)
            if odu.performance_data
            else None
        ),
        entity_registry_enabled_default=False,
    ),
    ODUSensorDescription(
        key="exhaust_temperature",
        name="Exhaust Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda odu: (
            _normalize_temperature(odu.performance_data.exhaust_temperature_c)
            if odu.performance_data
            else None
        ),
        entity_registry_enabled_default=False,
    ),
    ODUSensorDescription(
        key="compressor_frequency",
        name="Compressor Frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda odu: (
            odu.performance_data.compressor_frequency_hz
            if odu.performance_data
            else None
        ),
        entity_registry_enabled_default=False,
    ),
    ODUSensorDescription(
        key="high_pressure",
        name="High-Side Pressure",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPressure.KPA,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda odu: (
            odu.performance_data.high_pressure_kpa if odu.performance_data else None
        ),
        entity_registry_enabled_default=False,
    ),
    ODUSensorDescription(
        key="low_pressure",
        name="Low-Side Pressure",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPressure.KPA,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda odu: (
            odu.performance_data.low_pressure_kpa if odu.performance_data else None
        ),
        entity_registry_enabled_default=False,
    ),
)


# ── Controller (Dial) sensors ─────────────────────────────────────────────────


@dataclass(frozen=True, kw_only=True)
class ControllerSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Controller], Any] = lambda _: None
    available_fn: Callable[[Controller], bool] = lambda ctrl: ctrl.is_online


CONTROLLER_SENSOR_DESCRIPTIONS: tuple[ControllerSensorDescription, ...] = (
    ControllerSensorDescription(
        key="ambient_temperature",
        name="Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        # Primary sensor - no category
        value_fn=lambda ctrl: _normalize_temperature(ctrl.ambient_temperature_c),
    ),
    ControllerSensorDescription(
        key="pcb_temperature_a",
        name="PCB Temperature A",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda ctrl: _normalize_temperature(ctrl.pcb_temperature_a_c),
        entity_registry_enabled_default=False,
    ),
    ControllerSensorDescription(
        key="pcb_temperature_b",
        name="PCB Temperature B",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda ctrl: _normalize_temperature(ctrl.pcb_temperature_b_c),
        entity_registry_enabled_default=False,
    ),
    ControllerSensorDescription(
        key="calibrated_ambient_temperature",
        name="Calibrated Ambient",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda ctrl: _normalize_temperature(ctrl.calibrated_ambient_c),
        entity_registry_enabled_default=False,
    ),
    ControllerSensorDescription(
        key="wifi_signal",
        name="WiFi Signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="dBm",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda ctrl: ctrl.wifi_signal_dbm,
        entity_registry_enabled_default=False,
    ),
    ControllerSensorDescription(
        key="wifi_frequency",
        name="WiFi Frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.MEGAHERTZ,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda ctrl: ctrl.wifi_freq_mhz,
        available_fn=lambda ctrl: ctrl.is_online and ctrl.wifi_freq_mhz is not None,
        entity_registry_enabled_default=False,
    ),
    ControllerSensorDescription(
        key="local_comms_health",
        name="Local Comms Health",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda ctrl: _local_comms_health_name(ctrl.local_comms_health),
        entity_registry_enabled_default=False,
    ),
)


# ── RemoteSensor (IDU-paired wireless sensor) ─────────────────────────────────


@dataclass(frozen=True, kw_only=True)
class RemoteSensorDescription(SensorEntityDescription):
    value_fn: Callable[[RemoteSensor], Any] = lambda _: None


REMOTE_SENSOR_DESCRIPTIONS: tuple[RemoteSensorDescription, ...] = (
    RemoteSensorDescription(
        key="temperature",
        name="Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        # Primary sensor - no category
        value_fn=lambda rs: _normalize_temperature(rs.ambient_temperature_c),
    ),
    RemoteSensorDescription(
        key="humidity",
        name="Humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        # Primary sensor - no category
        value_fn=lambda rs: rs.humidity_percent,
    ),
    RemoteSensorDescription(
        key="battery",
        name="Battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda rs: rs.battery_level_percent,
    ),
    RemoteSensorDescription(
        key="signal_strength",
        name="Signal Strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="dBm",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda rs: rs.signal_level_dbm,
        entity_registry_enabled_default=False,
    ),
)


# ── ControllerRemoteSensor (Dial-paired wireless sensor) ──────────────────────


@dataclass(frozen=True, kw_only=True)
class ControllerRemoteSensorDescription(SensorEntityDescription):
    value_fn: Callable[[ControllerRemoteSensor], Any] = lambda _: None


CONTROLLER_REMOTE_SENSOR_DESCRIPTIONS: tuple[ControllerRemoteSensorDescription, ...] = (
    ControllerRemoteSensorDescription(
        key="temperature",
        name="Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        # Primary sensor - no category
        value_fn=lambda crs: _normalize_temperature(crs.ambient_temperature_c),
    ),
    ControllerRemoteSensorDescription(
        key="humidity",
        name="Humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        # Primary sensor - no category
        value_fn=lambda crs: crs.humidity_percent,
    ),
    ControllerRemoteSensorDescription(
        key="battery",
        name="Battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda crs: crs.battery_level_percent,
    ),
    ControllerRemoteSensorDescription(
        key="signal_strength",
        name="Signal Strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="dBm",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda crs: crs.signal_level_dbm,
        entity_registry_enabled_default=False,
    ),
)


# ── Platform setup ────────────────────────────────────────────────────────────


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: QuiltConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensor entities from a config entry."""
    coordinator = entry.runtime_data
    snapshot = coordinator.data
    entities: list[SensorEntity] = []

    # Index the first IDU per space so space-level sensors have a device to live on.
    first_idu_for_space: dict[str, str] = {}
    for idu in snapshot.indoor_units:
        if idu.space_id and idu.space_id not in first_idu_for_space:
            first_idu_for_space[idu.space_id] = idu.id

    # Space temperature sensors — attached to the first IDU in each space
    for space in snapshot.spaces:
        if not space.is_room:
            continue
        idu_id = first_idu_for_space.get(space.id)
        if idu_id is None:
            continue
        for space_desc in SPACE_SENSOR_DESCRIPTIONS:
            entities.append(QuiltSpaceSensor(coordinator, space.id, idu_id, space_desc))

    # Energy sensors — one per room space, on the IDU device
    for space in snapshot.spaces:
        if not space.is_room:
            continue
        idu_id = first_idu_for_space.get(space.id)
        if idu_id is None:
            continue
        entities.append(QuiltEnergySensor(coordinator, space.id, idu_id))

    # QSM/IDU sensors
    for idu in snapshot.indoor_units:
        for idu_desc in IDU_SENSOR_DESCRIPTIONS:
            entities.append(QuiltIDUSensor(coordinator, idu.id, idu_desc))
        if idu.qsm_id:
            for qsm_desc in QSM_SENSOR_DESCRIPTIONS:
                entities.append(QuiltQSMSensor(coordinator, idu.id, qsm_desc))

    # OutdoorUnit sensors — one set per ODU, linked via the first IDU that
    # references it.
    # An ODU can serve multiple IDUs (multi-zone), so iterating over IDUs would create
    # duplicate sensor sets for the same ODU device.
    odu_to_first_idu: dict[str, str] = {}
    for idu in snapshot.indoor_units:
        if idu.outdoor_unit_id and idu.outdoor_unit_id not in odu_to_first_idu:
            odu_to_first_idu[idu.outdoor_unit_id] = idu.id
    for odu_id, idu_id in odu_to_first_idu.items():
        odu = coordinator.odu_by_id.get(odu_id)
        if not odu:
            continue
        for odu_desc in ODU_SENSOR_DESCRIPTIONS:
            entities.append(QuiltODUSensor(coordinator, odu_id, idu_id, odu_desc))

    # Controller (Dial) sensors
    for ctrl in snapshot.controllers:
        for ctrl_desc in CONTROLLER_SENSOR_DESCRIPTIONS:
            entities.append(QuiltControllerSensor(coordinator, ctrl.id, ctrl_desc))

    # RemoteSensor sensors (IDU-paired wireless sensors)
    for rs in snapshot.remote_sensors:
        for rs_desc in REMOTE_SENSOR_DESCRIPTIONS:
            entities.append(QuiltRemoteSensor(coordinator, rs.id, rs_desc))

    # ControllerRemoteSensor sensors (Dial-paired wireless sensors)
    for crs in snapshot.controller_remote_sensors:
        for crs_desc in CONTROLLER_REMOTE_SENSOR_DESCRIPTIONS:
            entities.append(QuiltControllerRemoteSensor(coordinator, crs.id, crs_desc))

    async_add_entities(entities)


# ── Sensor entity classes ─────────────────────────────────────────────────────


class QuiltSpaceSensor(QuiltEntity, SensorEntity):
    """Space temperature sensor, presented on the first IDU in the space."""

    entity_description: SpaceSensorDescription

    def __init__(
        self,
        coordinator: QuiltCoordinator,
        space_id: str,
        idu_id: str,
        description: SpaceSensorDescription,
    ) -> None:
        """Initialize the space sensor entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._space_id: str = space_id
        self._idu_id: str = idu_id
        self._attr_unique_id: str = f"quilt_space_{space_id}_{description.key}"

    @property
    def _space(self) -> Space:
        return self.coordinator.spaces_by_id[self._space_id]

    @property
    @override
    def device_info(self) -> DeviceInfo:
        idu = self.coordinator.idu_by_id[self._idu_id]
        space = self._space
        return idu_device_info(idu, space)

    @property
    @override
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._space)


class QuiltIDUSensor(QuiltEntity, SensorEntity):
    """Sensor entity for a Quilt indoor unit."""

    entity_description: IDUSensorDescription

    def __init__(
        self,
        coordinator: QuiltCoordinator,
        idu_id: str,
        description: IDUSensorDescription,
    ) -> None:
        """Initialize the indoor unit sensor entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._idu_id: str = idu_id
        self._attr_unique_id: str = f"quilt_idu_{idu_id}_{description.key}"

    @property
    def _idu(self) -> IndoorUnit:
        return self.coordinator.idu_by_id[self._idu_id]

    @property
    @override
    def device_info(self) -> DeviceInfo:
        idu = self._idu
        space = (
            self.coordinator.spaces_by_id.get(idu.space_id) if idu.space_id else None
        )
        return idu_device_info(idu, space)

    @property
    @override
    def available(self) -> bool:
        return super().available and self.entity_description.available_fn(self._idu)

    @property
    @override
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._idu)


class QuiltODUSensor(QuiltEntity, SensorEntity):
    """Sensor entity for a Quilt outdoor unit."""

    entity_description: ODUSensorDescription

    def __init__(
        self,
        coordinator: QuiltCoordinator,
        odu_id: str,
        idu_id: str,
        description: ODUSensorDescription,
    ) -> None:
        """Initialize the outdoor unit sensor entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._odu_id: str = odu_id
        self._idu_id: str = idu_id
        self._attr_unique_id: str = f"quilt_odu_{odu_id}_{description.key}"

    @property
    def _odu(self) -> OutdoorUnit:
        return self.coordinator.odu_by_id[self._odu_id]

    @property
    @override
    def device_info(self) -> DeviceInfo:
        idu = self.coordinator.idu_by_id.get(self._idu_id)
        return odu_device_info(self._odu, idu)

    @property
    @override
    def available(self) -> bool:
        return super().available and self.entity_description.available_fn(self._odu)

    @property
    @override
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._odu)


class QuiltControllerSensor(QuiltEntity, SensorEntity):
    """Sensor entity for a Quilt Controller (Dial)."""

    entity_description: ControllerSensorDescription

    def __init__(
        self,
        coordinator: QuiltCoordinator,
        ctrl_id: str,
        description: ControllerSensorDescription,
    ) -> None:
        """Initialize the controller sensor entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._ctrl_id: str = ctrl_id
        self._attr_unique_id: str = f"quilt_ctrl_{ctrl_id}_{description.key}"

    @property
    def _ctrl(self) -> Controller:
        return self.coordinator.ctrl_by_id[self._ctrl_id]

    @property
    @override
    def device_info(self) -> DeviceInfo:
        ctrl = self._ctrl
        idu = (
            self.coordinator.idu_by_space_id.get(ctrl.space_id)
            if ctrl.space_id
            else None
        )
        return controller_device_info(ctrl, idu)

    @property
    @override
    def available(self) -> bool:
        return super().available and self.entity_description.available_fn(self._ctrl)

    @property
    @override
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._ctrl)


class QuiltQSMSensor(QuiltEntity, SensorEntity):
    """Sensor entity for QSM radar/ALS data, presented on the IDU device."""

    entity_description: QSMSensorDescription

    def __init__(
        self,
        coordinator: QuiltCoordinator,
        idu_id: str,
        description: QSMSensorDescription,
    ) -> None:
        """Initialize the QSM sensor entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._idu_id: str = idu_id
        self._attr_unique_id: str = f"quilt_qsm_{idu_id}_{description.key}"

    @property
    def _idu(self) -> IndoorUnit:
        return self.coordinator.idu_by_id[self._idu_id]

    @property
    def _qsm(self) -> QuiltSmartModule | None:
        qsm_id = self._idu.qsm_id
        return self.coordinator.qsm_by_id.get(qsm_id) if qsm_id else None

    @property
    @override
    def device_info(self) -> DeviceInfo:
        idu = self._idu
        space = (
            self.coordinator.spaces_by_id.get(idu.space_id) if idu.space_id else None
        )
        return idu_device_info(idu, space)

    @property
    @override
    def available(self) -> bool:
        return super().available and self._idu.is_online and self._qsm is not None

    @property
    @override
    def native_value(self) -> Any:
        qsm = self._qsm
        return self.entity_description.value_fn(qsm) if qsm else None


class QuiltRemoteSensor(QuiltEntity, SensorEntity):
    """Sensor entity for a Quilt remote sensor (IDU-paired wireless sensor)."""

    entity_description: RemoteSensorDescription

    def __init__(
        self,
        coordinator: QuiltCoordinator,
        rs_id: str,
        description: RemoteSensorDescription,
    ) -> None:
        """Initialize the remote sensor entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._rs_id: str = rs_id
        self._attr_unique_id: str = f"quilt_rs_{rs_id}_{description.key}"

    @property
    def _rs(self) -> RemoteSensor:
        return self.coordinator.remote_sensor_by_id[self._rs_id]

    @property
    @override
    def device_info(self) -> DeviceInfo:
        rs = self._rs
        idu = self.coordinator.idu_by_id.get(rs.indoor_unit_id)
        return remote_sensor_device_info(rs, idu)

    @property
    @override
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._rs)


class QuiltControllerRemoteSensor(QuiltEntity, SensorEntity):
    """Sensor entity for a Quilt controller remote sensor (Dial-paired)."""

    entity_description: ControllerRemoteSensorDescription

    def __init__(
        self,
        coordinator: QuiltCoordinator,
        crs_id: str,
        description: ControllerRemoteSensorDescription,
    ) -> None:
        """Initialize the controller remote sensor entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._crs_id: str = crs_id
        self._attr_unique_id: str = f"quilt_crs_{crs_id}_{description.key}"

    @property
    def _crs(self) -> ControllerRemoteSensor:
        return self.coordinator.ctrl_remote_sensor_by_id[self._crs_id]

    @property
    @override
    def device_info(self) -> DeviceInfo:
        crs = self._crs
        ctrl = self.coordinator.ctrl_by_id.get(crs.controller_id)
        return ctrl_remote_sensor_device_info(crs, ctrl)

    @property
    @override
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._crs)


class QuiltEnergySensor(QuiltEntity, SensorEntity):
    """Today's energy consumption for a Quilt space (room)."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.ENERGY
    _attr_state_class: SensorStateClass = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement: str = UnitOfEnergy.KILO_WATT_HOUR
    _attr_translation_key: str = "energy_today"
    _attr_suggested_display_precision: int = 3

    def __init__(
        self,
        coordinator: QuiltCoordinator,
        space_id: str,
        idu_id: str,
    ) -> None:
        """Initialize the energy sensor entity."""
        super().__init__(coordinator)
        self._space_id: str = space_id
        self._idu_id: str = idu_id
        self._attr_unique_id: str = f"quilt_space_{space_id}_energy_today"

    @property
    @override
    def device_info(self) -> DeviceInfo:
        idu = self.coordinator.idu_by_id[self._idu_id]
        space = self.coordinator.spaces_by_id.get(self._space_id)
        return idu_device_info(idu, space)

    @property
    @override
    def native_value(self) -> float | None:
        total = self.coordinator.energy_by_space_id.get(self._space_id)
        return round(total, 4) if total is not None else None

    @property
    @override
    def last_reset(self) -> datetime | None:
        return self.coordinator.energy_last_reset
