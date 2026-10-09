"""Button platform for Quilt Heat Pump.

Provides button entities for:
- QSM/IDU: start and cancel the indoor unit's diagnostic self-test (the Quilt
  app's "Run diagnostic test").

The self-test takes up to 30 minutes, during which the room can't be heated
or cooled; other indoor units on the same outdoor unit may wait in standby
meanwhile. Its results go to Quilt, not to Home Assistant. Both buttons are
disabled by default so the test can't be started by accident; follow its
progress with the "Self-test" binary sensor. Start is unavailable while a test
runs, and Cancel while none does.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, override

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from quilt_hp.models.indoor_unit import IndoorUnit

from .coordinator import QuiltCoordinator
from .entity import QuiltIDUEntity, async_setup_dynamic_entities

if TYPE_CHECKING:
    from . import QuiltConfigEntry

# Limit concurrent updates to avoid overwhelming the device
PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class IDUButtonDescription(ButtonEntityDescription):
    press_fn: Callable[[QuiltCoordinator, IndoorUnit], Awaitable[None]]
    # Whether the button can be pressed, given the unit's self-test state.
    available_fn: Callable[[QuiltCoordinator, IndoorUnit], bool]


IDU_BUTTON_DESCRIPTIONS: tuple[IDUButtonDescription, ...] = (
    IDUButtonDescription(
        key="start_self_test",
        translation_key="start_self_test",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        press_fn=lambda coordinator, idu: coordinator.async_start_self_test(idu),
        available_fn=lambda coordinator, idu: not coordinator.self_test_active(idu),
    ),
    IDUButtonDescription(
        key="cancel_self_test",
        translation_key="cancel_self_test",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        press_fn=lambda coordinator, idu: coordinator.async_cancel_self_test(idu),
        available_fn=lambda coordinator, idu: coordinator.self_test_active(idu),
    ),
)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: QuiltConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up button entities from a config entry."""
    coordinator = entry.runtime_data

    def _build_new(known: set[str]) -> list[tuple[str, ButtonEntity]]:
        new: list[tuple[str, ButtonEntity]] = []
        for idu in coordinator.data.indoor_units:
            key = f"idu_{idu.id}"
            if key in known:
                continue
            for desc in IDU_BUTTON_DESCRIPTIONS:
                new.append((key, QuiltIDUButton(coordinator, idu.id, desc)))
        return new

    async_setup_dynamic_entities(entry, coordinator, async_add_entities, _build_new)


class QuiltIDUButton(QuiltIDUEntity, ButtonEntity):
    """Button entity for a Quilt indoor unit action."""

    entity_description: IDUButtonDescription

    def __init__(
        self,
        coordinator: QuiltCoordinator,
        idu_id: str,
        description: IDUButtonDescription,
    ) -> None:
        """Initialize the indoor unit button entity."""
        super().__init__(coordinator, idu_id)
        self.entity_description = description
        self._attr_unique_id: str = f"quilt_idu_{idu_id}_{description.key}"

    @override
    def _model_available(self, idu: IndoorUnit) -> bool:
        return idu.is_online and self.entity_description.available_fn(
            self.coordinator, idu
        )

    @override
    async def async_press(self) -> None:
        await self.entity_description.press_fn(self.coordinator, self._idu)
        await self._async_refresh_if_not_streaming()
