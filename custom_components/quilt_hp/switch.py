"""Switch platform for Quilt Heat Pump.

Provides switch entities for:
- Schedule execution: pause/resume all schedules for the system (per Location).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from quilt_hp.models.system import Location

from .coordinator import QuiltCoordinator
from .entity import (
    QuiltEntity,
    async_setup_dynamic_entities,
    location_device_info,
)

if TYPE_CHECKING:
    from . import QuiltConfigEntry

# Limit concurrent updates to avoid overwhelming the device
PARALLEL_UPDATES = 1


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: QuiltConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up switch entities from a config entry."""
    coordinator = entry.runtime_data

    def _build_new(known: set[str]) -> list[tuple[str, SwitchEntity]]:
        return [
            (loc.id, QuiltScheduleSwitch(coordinator, loc.id))
            for loc in coordinator.data.locations
            if loc.id not in known
        ]

    async_setup_dynamic_entities(entry, coordinator, async_add_entities, _build_new)


class QuiltScheduleSwitch(QuiltEntity, SwitchEntity):
    """Switch that pauses or resumes all Quilt schedules for a location.

    ``is_on`` means schedules are *running* (not paused).
    Turning the switch off pauses all schedules; turning it on resumes them.
    """

    _attr_device_class: SwitchDeviceClass = SwitchDeviceClass.SWITCH
    _attr_translation_key: str = "schedules"

    def __init__(self, coordinator: QuiltCoordinator, location_id: str) -> None:
        """Initialize the schedule switch entity."""
        super().__init__(coordinator)
        self._location_id: str = location_id
        self._attr_unique_id: str = f"quilt_schedule_{location_id}"

    @property
    def _location(self) -> Location:
        return self.coordinator.location_by_id[self._location_id]

    @property
    @override
    def available(self) -> bool:
        return (
            super().available and self._location_id in self.coordinator.location_by_id
        )

    @property
    @override
    def device_info(self) -> DeviceInfo:
        return location_device_info(self._location)

    @property
    @override
    def is_on(self) -> bool:
        # True = schedules running (not paused)
        return not self._location.schedule_paused

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Resume all schedules."""
        await self.coordinator.async_set_schedule_execution(paused=False)
        # Location state is not carried on the stream — always poll.
        await self.coordinator.async_request_refresh()

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Pause all schedules."""
        await self.coordinator.async_set_schedule_execution(paused=True)
        # Location state is not carried on the stream — always poll.
        await self.coordinator.async_request_refresh()
