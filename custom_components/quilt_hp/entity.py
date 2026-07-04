"""Base entity classes for the Quilt Heat Pump integration."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import override

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from quilt_hp.models.controller import Controller
from quilt_hp.models.indoor_unit import IndoorUnit
from quilt_hp.models.outdoor_unit import OutdoorUnit
from quilt_hp.models.sensor import ControllerRemoteSensor, RemoteSensor
from quilt_hp.models.space import Space
from quilt_hp.models.system import Location

from .const import DOMAIN
from .coordinator import QuiltCoordinator

_MANUFACTURER: str = "Quilt"

_SENTINEL_VALUES: frozenset[str] = frozenset({"N/A", "n/a", "NA", ""})


def _clean(value: str | None) -> str | None:
    """Return None for missing/sentinel strings the API may return."""
    if value is None or value in _SENTINEL_VALUES:
        return None
    return value


def async_setup_dynamic_entities(
    entry: ConfigEntry,
    coordinator: QuiltCoordinator,
    async_add_entities: AddConfigEntryEntitiesCallback,
    build_new: Callable[[set[str]], Iterable[tuple[str, Entity]]],
) -> None:
    """Add entities now and whenever new devices appear in the snapshot.

    *build_new* receives the set of already-known keys and yields
    ``(key, entity)`` pairs for models not yet represented; it is invoked
    once at setup and again on every coordinator update so devices added
    to the Quilt account after setup appear without a reload.
    """
    known: set[str] = set()

    def _add_new() -> None:
        new_entities: list[Entity] = []
        for key, entity in build_new(known):
            known.add(key)
            new_entities.append(entity)
        if new_entities:
            async_add_entities(new_entities)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class QuiltEntity(CoordinatorEntity[QuiltCoordinator]):
    """Common properties for all Quilt entities."""

    _attr_has_entity_name: bool = True

    async def _async_refresh_if_not_streaming(self) -> None:
        """Request a coordinator poll only when the gRPC stream is not active.

        When the stream is running, state changes arrive within milliseconds and
        an immediate poll would be redundant. This method is called after every
        write operation to ensure state is refreshed even when the stream is down.
        """
        if not self.coordinator.is_streaming:
            await self.coordinator.async_request_refresh()


class QuiltIDUEntity(QuiltEntity):
    """Entity backed by an IndoorUnit, shown on the IDU device.

    Provides the IDU lookup, device info, and availability handling shared
    by the light, select, sensor, and binary sensor platforms.
    """

    def __init__(self, coordinator: QuiltCoordinator, idu_id: str) -> None:
        """Initialize the entity with its indoor unit id."""
        super().__init__(coordinator)
        self._idu_id: str = idu_id

    @property
    def _idu(self) -> IndoorUnit:
        return self.coordinator.idu_by_id[self._idu_id]

    def _model_available(self, idu: IndoorUnit) -> bool:
        """Return whether the entity is available given its (present) IDU."""
        return idu.is_online

    @property
    @override
    def available(self) -> bool:
        idu = self.coordinator.idu_by_id.get(self._idu_id)
        return super().available and idu is not None and self._model_available(idu)

    @property
    @override
    def device_info(self) -> DeviceInfo:
        idu = self._idu
        space = (
            self.coordinator.spaces_by_id.get(idu.space_id) if idu.space_id else None
        )
        return idu_device_info(idu, space)


class QuiltControllerEntity(QuiltEntity):
    """Entity backed by a Controller (Dial), shown on the Dial device."""

    def __init__(self, coordinator: QuiltCoordinator, ctrl_id: str) -> None:
        """Initialize the entity with its controller id."""
        super().__init__(coordinator)
        self._ctrl_id: str = ctrl_id

    @property
    def _ctrl(self) -> Controller:
        return self.coordinator.ctrl_by_id[self._ctrl_id]

    def _model_available(self, ctrl: Controller) -> bool:
        """Return whether the entity is available given its (present) controller."""
        return ctrl.is_online

    @property
    @override
    def available(self) -> bool:
        ctrl = self.coordinator.ctrl_by_id.get(self._ctrl_id)
        return super().available and ctrl is not None and self._model_available(ctrl)

    @property
    @override
    def device_info(self) -> DeviceInfo:
        ctrl = self._ctrl
        idu = (
            self.coordinator.idu_by_space_id.get(ctrl.space_id)
            if ctrl.space_id
            else None
        )
        space = (
            self.coordinator.spaces_by_id.get(ctrl.space_id) if ctrl.space_id else None
        )
        return controller_device_info(ctrl, idu, space)


def _is_serial_default_name(name: str, serial: str | None) -> bool:
    """Return True when *name* is Quilt's serial-based auto-generated default.

    Quilt names indoor units "Indoor Unit {serial}" and dials "Dial {serial}"
    by default. Such names duplicate the serial (already shown on the device
    card) and aren't user-friendly, so callers prefer the room name instead.
    """
    return serial is not None and serial != "" and serial in name


def idu_device_info(idu: IndoorUnit, space: Space | None = None) -> DeviceInfo:
    """Build a ``DeviceInfo`` for an IDU and its embedded QSM.

    The device is named after the room (space) it serves, unless the IDU has a
    genuine user-set name in the Quilt app. Quilt's serial-based default name
    ("Indoor Unit {serial}") is treated as no name, since the serial is already
    exposed on the device card.

    Spaces are not HA devices; they are surfaced as areas via ``suggested_area``.
    """
    configured = idu.settings.name
    if configured and not _is_serial_default_name(configured, idu.serial_number):
        name = configured
    elif space is not None:
        name = f"{space.name} Indoor Unit"
    elif configured:
        name = configured
    else:
        name = f"Indoor Unit {idu.id[:8]}"

    info = DeviceInfo(
        identifiers={(DOMAIN, f"i_{idu.id}")},
        name=name,
        manufacturer=_MANUFACTURER,
        model=_clean(idu.model_sku) or "Indoor Unit",
    )
    if _clean(idu.serial_number):
        info["serial_number"] = idu.serial_number
    if _clean(idu.firmware_version):
        info["sw_version"] = idu.firmware_version
    if space is not None:
        info["suggested_area"] = space.name
    return info


def odu_device_info(odu: OutdoorUnit, idu: IndoorUnit | None = None) -> DeviceInfo:
    """Build a ``DeviceInfo`` for an outdoor unit.

    Uses the serial number when available to create a more identifiable device name.
    The ODU is linked to the IDU in the same space so HA groups them
    correctly in the UI.
    """
    # Use serial number for better identification if available
    serial = _clean(odu.serial_number)
    name = f"Outdoor Unit {serial}" if serial else f"Outdoor Unit {odu.id[:8]}"

    info = DeviceInfo(
        identifiers={(DOMAIN, f"u_{odu.id}")},
        name=name,
        manufacturer=_MANUFACTURER,
        model=_clean(odu.model_sku) or "Outdoor Unit",
    )
    if serial:
        info["serial_number"] = serial
    if _clean(odu.firmware_version):
        info["sw_version"] = odu.firmware_version
    if idu is not None:
        info["via_device"] = (DOMAIN, f"i_{idu.id}")
    return info


def controller_device_info(
    ctrl: Controller, idu: IndoorUnit | None = None, space: Space | None = None
) -> DeviceInfo:
    """Build a ``DeviceInfo`` for a Quilt Controller (Dial).

    Named after the room (space) it serves, unless the Dial has a genuine
    user-set name in the Quilt app; Quilt's serial-based default ("Dial
    {serial}") is treated as no name since the serial is on the device card.

    The Dial is a physically separate device from the IDU. ``via_device`` links
    it to the IDU in the same space so HA groups them correctly in the UI.
    """
    configured = ctrl.name
    if configured and not _is_serial_default_name(configured, ctrl.serial_number):
        name = configured
    elif space is not None:
        name = f"{space.name} Dial"
    elif configured:
        name = configured
    else:
        name = "Quilt Dial"

    info = DeviceInfo(
        identifiers={(DOMAIN, f"c_{ctrl.id}")},
        name=name,
        manufacturer=_MANUFACTURER,
        model=_clean(ctrl.model_sku) or "Dial",
    )
    if _clean(ctrl.serial_number):
        info["serial_number"] = ctrl.serial_number
    if _clean(ctrl.firmware_version):
        info["sw_version"] = ctrl.firmware_version
    if idu is not None:
        info["via_device"] = (DOMAIN, f"i_{idu.id}")
    return info


def remote_sensor_device_info(
    rs: RemoteSensor, idu: IndoorUnit | None = None
) -> DeviceInfo:
    """Build a ``DeviceInfo`` for a Quilt remote sensor (IDU-paired wireless sensor).

    Uses a unique identifier to distinguish multiple sensors.
    """
    name = f"Remote Sensor {rs.id[:8]}"
    info = DeviceInfo(
        identifiers={(DOMAIN, f"rs_{rs.id}")},
        name=name,
        manufacturer=_MANUFACTURER,
        model="Remote Sensor",
    )
    if idu is not None:
        info["via_device"] = (DOMAIN, f"i_{idu.id}")
    return info


def ctrl_remote_sensor_device_info(
    crs: ControllerRemoteSensor, ctrl: Controller | None = None
) -> DeviceInfo:
    """Build a ``DeviceInfo`` for a Quilt controller remote sensor (Dial-paired).

    Includes controller context when available for better identification.
    """
    if ctrl and ctrl.name:
        name = f"{ctrl.name} Zone Sensor"
    else:
        name = f"Zone Sensor {crs.id[:8]}"

    info = DeviceInfo(
        identifiers={(DOMAIN, f"crs_{crs.id}")},
        name=name,
        manufacturer=_MANUFACTURER,
        model="Zone Sensor",
    )
    if ctrl is not None:
        info["via_device"] = (DOMAIN, f"c_{ctrl.id}")
    return info


def location_device_info(location: Location) -> DeviceInfo:
    """Build a ``DeviceInfo`` for a Quilt location (home/system)."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"loc_{location.id}")},
        name=location.name or "Quilt Home",
        manufacturer=_MANUFACTURER,
        model="Quilt System",
    )
