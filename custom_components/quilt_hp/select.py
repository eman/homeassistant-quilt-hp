"""Select platform for Quilt Heat Pump — fan speed and louver controls per IDU."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from quilt_hp.models.enums import FanSpeed, LouverAngle, LouverMode

from .coordinator import QuiltCoordinator
from .entity import QuiltIDUEntity, async_setup_dynamic_entities

if TYPE_CHECKING:
    from . import QuiltConfigEntry

# Limit concurrent updates to avoid overwhelming the device
PARALLEL_UPDATES = 1

# Quilt fan speeds. AUTO (the HVAC system chooses the speed) is the default;
# there is no "off" — the fan is always in one of these modes.
_STR_TO_FAN_SPEED: dict[str, FanSpeed] = {
    "auto": FanSpeed.AUTO,
    "quiet": FanSpeed.QUIET,
    "low": FanSpeed.LOW,
    "medium": FanSpeed.MEDIUM,
    "high": FanSpeed.HIGH,
    "blast": FanSpeed.BLAST,
}

_FAN_SPEED_TO_STR: dict[FanSpeed, str] = {v: k for k, v in _STR_TO_FAN_SPEED.items()}

_FAN_SPEED_OPTIONS: list[str] = list(_STR_TO_FAN_SPEED)

_STR_TO_LOUVER_MODE: dict[str, LouverMode] = {
    "closed": LouverMode.CLOSED,
    "sweep": LouverMode.SWEEP,
    "fixed": LouverMode.FIXED,
    "auto": LouverMode.AUTO,
}

_LOUVER_MODE_TO_STR: dict[LouverMode, str] = {
    v: k for k, v in _STR_TO_LOUVER_MODE.items()
}

# Lowercase option keys with translated display names in strings.json.
_STR_TO_LOUVER_ANGLE: dict[str, LouverAngle] = {
    "horizontal": LouverAngle.ANGLE1,
    "slightly_down": LouverAngle.ANGLE2,
    "down": LouverAngle.ANGLE3,
    "mostly_down": LouverAngle.ANGLE4,
    "straight_down": LouverAngle.ANGLE5,
}

_LOUVER_ANGLE_TO_STR: dict[LouverAngle, str] = {
    v: k for k, v in _STR_TO_LOUVER_ANGLE.items()
}

_LOUVER_MODE_OPTIONS: list[str] = list(_STR_TO_LOUVER_MODE)
_LOUVER_ANGLE_OPTIONS: list[str] = list(_STR_TO_LOUVER_ANGLE)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: QuiltConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up select entities from a config entry."""
    coordinator = entry.runtime_data

    def _build_new(known: set[str]) -> list[tuple[str, SelectEntity]]:
        new: list[tuple[str, SelectEntity]] = []
        for idu in coordinator.data.indoor_units:
            if idu.id in known:
                continue
            new.append((idu.id, QuiltFanSpeedSelect(coordinator, idu.id)))
            new.append((idu.id, QuiltLouverModeSelect(coordinator, idu.id)))
            new.append((idu.id, QuiltLouverAngleSelect(coordinator, idu.id)))
        return new

    async_setup_dynamic_entities(entry, coordinator, async_add_entities, _build_new)


class QuiltFanSpeedSelect(QuiltIDUEntity, SelectEntity):
    """Select entity for indoor unit fan speed.

    Quilt's fan is always running in one of these modes; ``auto`` lets the
    HVAC system choose the speed. There is no "off" state.
    """

    _attr_options: list[str] = _FAN_SPEED_OPTIONS
    _attr_translation_key: str = "fan_speed"

    def __init__(self, coordinator: QuiltCoordinator, idu_id: str) -> None:
        """Initialize the fan speed select entity."""
        super().__init__(coordinator, idu_id)
        self._attr_unique_id: str = f"quilt_idu_fan_speed_{idu_id}"

    @property
    @override
    def current_option(self) -> str | None:
        return _FAN_SPEED_TO_STR.get(self._idu.controls.fan_speed)

    @override
    async def async_select_option(self, option: str) -> None:
        fan_speed = _STR_TO_FAN_SPEED[option]
        await self.coordinator.async_set_indoor_unit(self._idu, fan_speed=fan_speed)
        await self._async_refresh_if_not_streaming()


class QuiltLouverModeSelect(QuiltIDUEntity, SelectEntity):
    """Select entity for indoor unit louver mode."""

    _attr_options: list[str] = _LOUVER_MODE_OPTIONS
    _attr_translation_key: str = "louver_mode"

    def __init__(self, coordinator: QuiltCoordinator, idu_id: str) -> None:
        """Initialize the louver mode select entity."""
        super().__init__(coordinator, idu_id)
        self._attr_unique_id: str = f"quilt_idu_louver_mode_{idu_id}"

    @property
    @override
    def current_option(self) -> str | None:
        return _LOUVER_MODE_TO_STR.get(self._idu.controls.louver_mode)

    @override
    async def async_select_option(self, option: str) -> None:
        mode = _STR_TO_LOUVER_MODE[option]
        await self.coordinator.async_set_indoor_unit(self._idu, louver_mode=mode)
        await self._async_refresh_if_not_streaming()


class QuiltLouverAngleSelect(QuiltIDUEntity, SelectEntity):
    """Select entity for indoor unit louver angle (relevant when mode=FIXED)."""

    _attr_options: list[str] = _LOUVER_ANGLE_OPTIONS
    _attr_translation_key: str = "louver_angle"

    def __init__(self, coordinator: QuiltCoordinator, idu_id: str) -> None:
        """Initialize the louver angle select entity."""
        super().__init__(coordinator, idu_id)
        self._attr_unique_id: str = f"quilt_idu_louver_angle_{idu_id}"

    @property
    @override
    def current_option(self) -> str | None:
        angle = LouverAngle.from_wire(self._idu.controls.louver_fixed_position)
        return _LOUVER_ANGLE_TO_STR.get(angle)

    @override
    async def async_select_option(self, option: str) -> None:
        angle = _STR_TO_LOUVER_ANGLE[option]
        await self.coordinator.async_set_indoor_unit(
            self._idu,
            louver_mode=LouverMode.FIXED,
            louver_position=angle.to_wire(),
        )
        await self._async_refresh_if_not_streaming()
