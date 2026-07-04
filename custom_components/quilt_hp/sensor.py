"""Sensor platform for Quilt Heat Pump.

Provides sensor entities for:
- Space: space temperature (space-calibrated), active comfort setting
- QSM/IDU: unit temp, humidity,
           inlet/outlet temp, presence level,
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
from typing import TYPE_CHECKING, Any, override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    LIGHT_LUX,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfPressure,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from quilt_hp.models.comfort import ComfortSetting
from quilt_hp.models.controller import Controller
from quilt_hp.models.enums import ComfortSettingType, LocalCommsHealthStatus
from quilt_hp.models.indoor_unit import IndoorUnit
from quilt_hp.models.outdoor_unit import OutdoorUnit
from quilt_hp.models.qsm import QuiltSmartModule
from quilt_hp.models.sensor import ControllerRemoteSensor, RemoteSensor
from quilt_hp.models.space import Space

from .coordinator import QuiltCoordinator
from .entity import (
    QuiltControllerEntity,
    QuiltEntity,
    QuiltIDUEntity,
    async_setup_dynamic_entities,
    ctrl_remote_sensor_device_info,
    odu_device_info,
    remote_sensor_device_info,
)
from .utils import normalize_float

if TYPE_CHECKING:
    from . import QuiltConfigEntry

# Read-only coordinator-driven platform — no request throttling needed.
PARALLEL_UPDATES = 0

_COMMS_HEALTH_OPTIONS: list[str] = [m.name.lower() for m in LocalCommsHealthStatus]

# Comfort-setting types Quilt actually applies to a room, as ENUM sensor options.
# UNSPECIFIED is a placeholder ("no active comfort setting") and is surfaced as
# an unknown/None state rather than an option.
_COMFORT_SETTING_OPTIONS: list[str] = [
    t.name.lower()
    for t in ComfortSettingType
    if t is not ComfortSettingType.UNSPECIFIED
]


def _local_comms_health_name(health: LocalCommsHealthStatus | None) -> str | None:
    """Return the lowercase health enum name, preserving falsy enum values."""
    if health is None:
        return None
    return health.name.lower()


def _rounded(value: float | None, digits: int) -> float | None:
    """Round *value*, passing through None/NaN as None."""
    normalized = normalize_float(value)
    return round(normalized, digits) if normalized is not None else None


# ── Space temperature sensor (on QSM device) ──────────────────────────────────


@dataclass(frozen=True, kw_only=True)
class SpaceSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Space], Any] = lambda _: None


SPACE_SENSOR_DESCRIPTIONS: tuple[SpaceSensorDescription, ...] = (
    SpaceSensorDescription(
        key="space_temperature",
        translation_key="space_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda space: (
            None
            if space.state.has_missing_ambient_temperature
            else normalize_float(space.state.ambient_temperature_c)
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
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda idu: normalize_float(idu.state.ambient_temperature_c),
    ),
    IDUSensorDescription(
        key="ambient_humidity",
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda idu: normalize_float(idu.state.ambient_humidity_percent),
    ),
    IDUSensorDescription(
        key="inlet_temperature",
        translation_key="inlet_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: normalize_float(idu.state.inlet_temperature_c),
        entity_registry_enabled_default=False,
    ),
    IDUSensorDescription(
        key="outlet_temperature",
        translation_key="outlet_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: normalize_float(idu.state.outlet_temperature_c),
        entity_registry_enabled_default=False,
    ),
    IDUSensorDescription(
        key="presence_level",
        translation_key="presence_level",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: _rounded(
            normalized * 100
            if (normalized := normalize_float(idu.state.presence_detection_level))
            is not None
            else None,
            1,
        ),
        entity_registry_enabled_default=False,
    ),
    IDUSensorDescription(
        key="hvac_capacity",
        translation_key="hvac_capacity",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: (
            normalize_float(idu.performance_metrics.capacity_w)
            if idu.performance_metrics
            else None
        ),
        entity_registry_enabled_default=False,
    ),
    IDUSensorDescription(
        key="hvac_power",
        translation_key="hvac_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: (
            normalize_float(idu.performance_metrics.hvac_power_w)
            if idu.performance_metrics
            else None
        ),
        entity_registry_enabled_default=False,
    ),
    IDUSensorDescription(
        key="led_power",
        translation_key="led_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: (
            normalize_float(idu.performance_metrics.led_power_w)
            if idu.performance_metrics
            else None
        ),
        entity_registry_enabled_default=False,
    ),
    IDUSensorDescription(
        key="coefficient_of_performance",
        translation_key="coefficient_of_performance",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: (
            _rounded(idu.performance_metrics.coefficient_of_performance, 2)
            if idu.performance_metrics
            else None
        ),
        entity_registry_enabled_default=False,
    ),
    IDUSensorDescription(
        key="calculated_ambient_temperature",
        translation_key="calibrated_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: normalize_float(
            idu.state.calculated_ambient_temperature_c
        ),
        entity_registry_enabled_default=False,
    ),
    # Performance data sensors (detailed refrigerant / heat-exchanger telemetry)
    IDUSensorDescription(
        key="coil_temperature",
        translation_key="coil_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: (
            normalize_float(idu.performance_data.coil_temperature_c)
            if idu.performance_data
            else None
        ),
        entity_registry_enabled_default=False,
    ),
    IDUSensorDescription(
        key="gas_pipe_temperature",
        translation_key="gas_pipe_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: (
            normalize_float(idu.performance_data.gas_pipe_temperature_c)
            if idu.performance_data
            else None
        ),
        entity_registry_enabled_default=False,
    ),
    IDUSensorDescription(
        key="liquid_pipe_temperature",
        translation_key="liquid_pipe_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: (
            normalize_float(idu.performance_data.liquid_pipe_temperature_c)
            if idu.performance_data
            else None
        ),
        entity_registry_enabled_default=False,
    ),
    IDUSensorDescription(
        key="inlet_humidity_perf",
        translation_key="inlet_humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: (
            normalize_float(idu.performance_data.inlet_humidity_pct)
            if idu.performance_data
            else None
        ),
        entity_registry_enabled_default=False,
    ),
    IDUSensorDescription(
        key="module_power",
        translation_key="module_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: (
            _rounded(
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
        translation_key="motion_signal",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda qsm: (
            normalize_float(qsm.sensors.phase_detected_raw) if qsm.sensors else None
        ),
        entity_registry_enabled_default=False,
    ),
    QSMSensorDescription(
        key="target_detected_raw",
        translation_key="presence_signal",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda qsm: (
            normalize_float(qsm.sensors.target_detected_raw) if qsm.sensors else None
        ),
        entity_registry_enabled_default=False,
    ),
    QSMSensorDescription(
        key="als_illuminance",
        translation_key="illuminance",
        device_class=SensorDeviceClass.ILLUMINANCE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=LIGHT_LUX,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda qsm: (
            normalize_float(qsm.sensors.als_illuminance_raw) if qsm.sensors else None
        ),
        entity_registry_enabled_default=False,
    ),
    QSMSensorDescription(
        key="local_comms_health",
        translation_key="local_comms_health",
        device_class=SensorDeviceClass.ENUM,
        options=_COMMS_HEALTH_OPTIONS,
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
        translation_key="outdoor_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        # Primary sensor - no category
        value_fn=lambda odu: (
            normalize_float(odu.performance_data.ambient_temperature_c)
            if odu.performance_data
            else None
        ),
    ),
    ODUSensorDescription(
        key="coil_temperature",
        translation_key="coil_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda odu: (
            normalize_float(odu.performance_data.coil_temperature_c)
            if odu.performance_data
            else None
        ),
        entity_registry_enabled_default=False,
    ),
    ODUSensorDescription(
        key="exhaust_temperature",
        translation_key="exhaust_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda odu: (
            normalize_float(odu.performance_data.exhaust_temperature_c)
            if odu.performance_data
            else None
        ),
        entity_registry_enabled_default=False,
    ),
    ODUSensorDescription(
        key="compressor_frequency",
        translation_key="compressor_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda odu: (
            normalize_float(odu.performance_data.compressor_frequency_hz)
            if odu.performance_data
            else None
        ),
        entity_registry_enabled_default=False,
    ),
    ODUSensorDescription(
        key="high_pressure",
        translation_key="high_side_pressure",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPressure.KPA,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda odu: (
            normalize_float(odu.performance_data.high_pressure_kpa)
            if odu.performance_data
            else None
        ),
        entity_registry_enabled_default=False,
    ),
    ODUSensorDescription(
        key="low_pressure",
        translation_key="low_side_pressure",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPressure.KPA,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda odu: (
            normalize_float(odu.performance_data.low_pressure_kpa)
            if odu.performance_data
            else None
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
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        # Primary sensor - no category
        value_fn=lambda ctrl: normalize_float(ctrl.ambient_temperature_c),
    ),
    ControllerSensorDescription(
        key="pcb_temperature_a",
        translation_key="pcb_temperature_a",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda ctrl: normalize_float(ctrl.pcb_temperature_a_c),
        entity_registry_enabled_default=False,
    ),
    ControllerSensorDescription(
        key="pcb_temperature_b",
        translation_key="pcb_temperature_b",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda ctrl: normalize_float(ctrl.pcb_temperature_b_c),
        entity_registry_enabled_default=False,
    ),
    ControllerSensorDescription(
        key="calibrated_ambient_temperature",
        translation_key="calibrated_ambient",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda ctrl: normalize_float(ctrl.calibrated_ambient_c),
        entity_registry_enabled_default=False,
    ),
    ControllerSensorDescription(
        key="wifi_signal",
        translation_key="wifi_signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda ctrl: normalize_float(ctrl.wifi_signal_dbm),
        entity_registry_enabled_default=False,
    ),
    ControllerSensorDescription(
        key="wifi_frequency",
        translation_key="wifi_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.MEGAHERTZ,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda ctrl: normalize_float(ctrl.wifi_freq_mhz),
        available_fn=lambda ctrl: ctrl.is_online and ctrl.wifi_freq_mhz is not None,
        entity_registry_enabled_default=False,
    ),
    ControllerSensorDescription(
        key="local_comms_health",
        translation_key="local_comms_health",
        device_class=SensorDeviceClass.ENUM,
        options=_COMMS_HEALTH_OPTIONS,
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
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        # Primary sensor - no category
        value_fn=lambda rs: normalize_float(rs.ambient_temperature_c),
    ),
    RemoteSensorDescription(
        key="humidity",
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        # Primary sensor - no category
        value_fn=lambda rs: normalize_float(rs.humidity_percent),
    ),
    RemoteSensorDescription(
        key="battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda rs: normalize_float(rs.battery_level_percent),
    ),
    RemoteSensorDescription(
        key="signal_strength",
        translation_key="signal_strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda rs: normalize_float(rs.signal_level_dbm),
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
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        # Primary sensor - no category
        value_fn=lambda crs: normalize_float(crs.ambient_temperature_c),
    ),
    ControllerRemoteSensorDescription(
        key="humidity",
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        # Primary sensor - no category
        value_fn=lambda crs: normalize_float(crs.humidity_percent),
    ),
    ControllerRemoteSensorDescription(
        key="battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda crs: normalize_float(crs.battery_level_percent),
    ),
    ControllerRemoteSensorDescription(
        key="signal_strength",
        translation_key="signal_strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda crs: normalize_float(crs.signal_level_dbm),
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

    def _build_new(known: set[str]) -> list[tuple[str, SensorEntity]]:
        snapshot = coordinator.data
        first_idu = coordinator.first_idu_id_by_space_id
        new: list[tuple[str, SensorEntity]] = []

        # Space temperature + energy sensors — on the first IDU in each space
        for space in snapshot.spaces:
            if not space.is_room:
                continue
            idu_id = first_idu.get(space.id)
            if idu_id is None or f"space_{space.id}" in known:
                continue
            key = f"space_{space.id}"
            for space_desc in SPACE_SENSOR_DESCRIPTIONS:
                new.append(
                    (key, QuiltSpaceSensor(coordinator, space.id, idu_id, space_desc))
                )
            new.append((key, QuiltComfortSettingSensor(coordinator, space.id, idu_id)))
            new.append((key, QuiltEnergySensor(coordinator, space.id, idu_id)))

        # QSM/IDU sensors
        for idu in snapshot.indoor_units:
            key = f"idu_{idu.id}"
            if key in known:
                continue
            for idu_desc in IDU_SENSOR_DESCRIPTIONS:
                new.append((key, QuiltIDUSensor(coordinator, idu.id, idu_desc)))
            if idu.qsm_id:
                for qsm_desc in QSM_SENSOR_DESCRIPTIONS:
                    new.append((key, QuiltQSMSensor(coordinator, idu.id, qsm_desc)))

        # OutdoorUnit sensors — one set per ODU, linked via the first IDU that
        # references it. An ODU can serve multiple IDUs (multi-zone), so
        # iterating over IDUs would create duplicate sensor sets.
        odu_to_first_idu: dict[str, str] = {}
        for idu in snapshot.indoor_units:
            if idu.outdoor_unit_id and idu.outdoor_unit_id not in odu_to_first_idu:
                odu_to_first_idu[idu.outdoor_unit_id] = idu.id
        for odu_id, idu_id in odu_to_first_idu.items():
            key = f"odu_{odu_id}"
            if key in known or odu_id not in coordinator.odu_by_id:
                continue
            for odu_desc in ODU_SENSOR_DESCRIPTIONS:
                new.append((key, QuiltODUSensor(coordinator, odu_id, idu_id, odu_desc)))

        # Controller (Dial) sensors
        for ctrl in snapshot.controllers:
            key = f"ctrl_{ctrl.id}"
            if key in known:
                continue
            for ctrl_desc in CONTROLLER_SENSOR_DESCRIPTIONS:
                new.append(
                    (key, QuiltControllerSensor(coordinator, ctrl.id, ctrl_desc))
                )

        # RemoteSensor sensors (IDU-paired wireless sensors)
        for rs in snapshot.remote_sensors:
            key = f"rs_{rs.id}"
            if key in known:
                continue
            for rs_desc in REMOTE_SENSOR_DESCRIPTIONS:
                new.append((key, QuiltRemoteSensor(coordinator, rs.id, rs_desc)))

        # ControllerRemoteSensor sensors (Dial-paired wireless sensors)
        for crs in snapshot.controller_remote_sensors:
            key = f"crs_{crs.id}"
            if key in known:
                continue
            for crs_desc in CONTROLLER_REMOTE_SENSOR_DESCRIPTIONS:
                new.append(
                    (key, QuiltControllerRemoteSensor(coordinator, crs.id, crs_desc))
                )

        return new

    async_setup_dynamic_entities(entry, coordinator, async_add_entities, _build_new)


# ── Sensor entity classes ─────────────────────────────────────────────────────


class QuiltSpaceSensor(QuiltIDUEntity, SensorEntity):
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
        super().__init__(coordinator, idu_id)
        self.entity_description = description
        self._space_id: str = space_id
        self._attr_unique_id: str = f"quilt_space_{space_id}_{description.key}"

    @property
    def _space(self) -> Space:
        return self.coordinator.spaces_by_id[self._space_id]

    @property
    @override
    def available(self) -> bool:
        return super().available and self._space_id in self.coordinator.spaces_by_id

    @property
    @override
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._space)


class QuiltComfortSettingSensor(QuiltIDUEntity, SensorEntity):
    """Diagnostic sensor for the comfort setting Quilt is applying to a space.

    Quilt's scheduler activates a comfort profile (Active/Sleep/Away/Standby/
    Custom) per room; this surfaces which one is currently active as an ENUM
    sensor keyed on the profile *type*. The profile's free-form name is exposed
    as the ``comfort_setting_name`` state attribute.
    """

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.ENUM
    _attr_entity_category: EntityCategory = EntityCategory.DIAGNOSTIC
    _attr_options: list[str] = _COMFORT_SETTING_OPTIONS
    _attr_translation_key: str = "active_comfort_setting"

    def __init__(
        self,
        coordinator: QuiltCoordinator,
        space_id: str,
        idu_id: str,
    ) -> None:
        """Initialize the active comfort setting sensor."""
        super().__init__(coordinator, idu_id)
        self._space_id: str = space_id
        self._attr_unique_id: str = f"quilt_space_{space_id}_active_comfort_setting"

    @property
    def _space(self) -> Space:
        return self.coordinator.spaces_by_id[self._space_id]

    @property
    def _active_comfort_setting(self) -> ComfortSetting | None:
        cs_id = self._space.controls.comfort_setting_id_or_none
        if cs_id is None:
            return None
        return self.coordinator.cs_by_id.get(cs_id)

    @property
    @override
    def available(self) -> bool:
        return super().available and self._space_id in self.coordinator.spaces_by_id

    @property
    @override
    def native_value(self) -> str | None:
        cs = self._active_comfort_setting
        if cs is None or cs.type is ComfortSettingType.UNSPECIFIED:
            return None
        return cs.type.name.lower()

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        cs = self._active_comfort_setting
        if cs is None or not cs.name:
            return None
        return {"comfort_setting_name": cs.name}


class QuiltIDUSensor(QuiltIDUEntity, SensorEntity):
    """Sensor entity for a Quilt indoor unit."""

    entity_description: IDUSensorDescription

    def __init__(
        self,
        coordinator: QuiltCoordinator,
        idu_id: str,
        description: IDUSensorDescription,
    ) -> None:
        """Initialize the indoor unit sensor entity."""
        super().__init__(coordinator, idu_id)
        self.entity_description = description
        self._attr_unique_id: str = f"quilt_idu_{idu_id}_{description.key}"

    @override
    def _model_available(self, idu: IndoorUnit) -> bool:
        return self.entity_description.available_fn(idu)

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
        odu = self.coordinator.odu_by_id.get(self._odu_id)
        return (
            super().available
            and odu is not None
            and self.entity_description.available_fn(odu)
        )

    @property
    @override
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._odu)


class QuiltControllerSensor(QuiltControllerEntity, SensorEntity):
    """Sensor entity for a Quilt Controller (Dial)."""

    entity_description: ControllerSensorDescription

    def __init__(
        self,
        coordinator: QuiltCoordinator,
        ctrl_id: str,
        description: ControllerSensorDescription,
    ) -> None:
        """Initialize the controller sensor entity."""
        super().__init__(coordinator, ctrl_id)
        self.entity_description = description
        self._attr_unique_id: str = f"quilt_ctrl_{ctrl_id}_{description.key}"

    @override
    def _model_available(self, ctrl: Controller) -> bool:
        return self.entity_description.available_fn(ctrl)

    @property
    @override
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._ctrl)


class QuiltQSMSensor(QuiltIDUEntity, SensorEntity):
    """Sensor entity for QSM radar/ALS data, presented on the IDU device."""

    entity_description: QSMSensorDescription

    def __init__(
        self,
        coordinator: QuiltCoordinator,
        idu_id: str,
        description: QSMSensorDescription,
    ) -> None:
        """Initialize the QSM sensor entity."""
        super().__init__(coordinator, idu_id)
        self.entity_description = description
        self._attr_unique_id: str = f"quilt_qsm_{idu_id}_{description.key}"

    @property
    def _qsm(self) -> QuiltSmartModule | None:
        qsm_id = self._idu.qsm_id
        return self.coordinator.qsm_by_id.get(qsm_id) if qsm_id else None

    @property
    @override
    def available(self) -> bool:
        return super().available and self._qsm is not None

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
    def available(self) -> bool:
        return super().available and self._rs_id in self.coordinator.remote_sensor_by_id

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
    def available(self) -> bool:
        return (
            super().available
            and self._crs_id in self.coordinator.ctrl_remote_sensor_by_id
        )

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


class QuiltEnergySensor(QuiltIDUEntity, SensorEntity):
    """Today's energy consumption for a Quilt space (room)."""

    _attr_device_class: SensorDeviceClass = SensorDeviceClass.ENERGY
    _attr_state_class: SensorStateClass = SensorStateClass.TOTAL
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
        super().__init__(coordinator, idu_id)
        self._space_id: str = space_id
        self._attr_unique_id: str = f"quilt_space_{space_id}_energy_today"

    @override
    def _model_available(self, idu: IndoorUnit) -> bool:
        # Energy data comes from the cloud API and remains valid while the
        # IDU itself is offline.
        return True

    @property
    @override
    def native_value(self) -> float | None:
        total = self.coordinator.energy_by_space_id.get(self._space_id)
        return round(total, 4) if total is not None else None

    @property
    @override
    def last_reset(self) -> datetime | None:
        return self.coordinator.energy_last_reset
