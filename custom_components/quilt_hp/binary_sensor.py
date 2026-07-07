"""Binary sensor platform for Quilt Heat Pump.

Provides binary sensor entities for:
- QSM/IDU: motion (phase radar), presence (target radar), occupied, online
- Controller (Dial): online
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from quilt_hp.models.controller import Controller
from quilt_hp.models.enums import OccupancyState, Presence
from quilt_hp.models.indoor_unit import IndoorUnit

from .coordinator import QuiltCoordinator
from .entity import (
    QuiltControllerEntity,
    QuiltIDUEntity,
    async_setup_dynamic_entities,
)

if TYPE_CHECKING:
    from . import QuiltConfigEntry

# Read-only coordinator-driven platform — no request throttling needed.
PARALLEL_UPDATES = 0

# ── IDU binary sensors ────────────────────────────────────────────────────────


@dataclass(frozen=True, kw_only=True)
class IDUBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[IndoorUnit], bool | None] = lambda _: None
    available_fn: Callable[[IndoorUnit], bool] = lambda idu: idu.is_online


IDU_BINARY_SENSOR_DESCRIPTIONS: tuple[IDUBinarySensorDescription, ...] = (
    IDUBinarySensorDescription(
        key="motion",
        translation_key="motion",
        device_class=BinarySensorDeviceClass.MOTION,
        value_fn=lambda idu: (
            idu.presence.sensor0_presence == Presence.DETECTED
            if idu.presence is not None
            else None
        ),
    ),
    IDUBinarySensorDescription(
        key="presence",
        translation_key="presence",
        device_class=BinarySensorDeviceClass.PRESENCE,
        value_fn=lambda idu: (
            idu.presence.sensor1_presence == Presence.DETECTED
            if idu.presence is not None
            else None
        ),
    ),
    IDUBinarySensorDescription(
        key="occupied",
        translation_key="occupied",
        device_class=BinarySensorDeviceClass.OCCUPANCY,
        value_fn=lambda idu: (
            None
            if (s := idu.effective_occupancy_state) is None
            else s == OccupancyState.DETECTED
        ),
    ),
    IDUBinarySensorDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda idu: idu.is_online,
        available_fn=lambda _: True,
        entity_registry_enabled_default=False,
    ),
)


# ── Controller binary sensors ─────────────────────────────────────────────────


@dataclass(frozen=True, kw_only=True)
class ControllerBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[Controller], bool | None] = lambda _: None
    available_fn: Callable[[Controller], bool] = lambda _: True


CONTROLLER_BINARY_SENSOR_DESCRIPTIONS: tuple[ControllerBinarySensorDescription, ...] = (
    ControllerBinarySensorDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda ctrl: ctrl.is_online,
        entity_registry_enabled_default=False,
    ),
)


# ── Platform setup ────────────────────────────────────────────────────────────


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: QuiltConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensor entities from a config entry."""
    coordinator = entry.runtime_data

    def _build_new(known: set[str]) -> list[tuple[str, BinarySensorEntity]]:
        new: list[tuple[str, BinarySensorEntity]] = []
        for idu in coordinator.data.indoor_units:
            key = f"idu_{idu.id}"
            if key in known:
                continue
            for desc in IDU_BINARY_SENSOR_DESCRIPTIONS:
                new.append((key, QuiltIDUBinarySensor(coordinator, idu.id, desc)))
        for ctrl in coordinator.data.controllers:
            key = f"ctrl_{ctrl.id}"
            if key in known:
                continue
            for ctrl_desc in CONTROLLER_BINARY_SENSOR_DESCRIPTIONS:
                new.append(
                    (key, QuiltControllerBinarySensor(coordinator, ctrl.id, ctrl_desc))
                )
        return new

    async_setup_dynamic_entities(entry, coordinator, async_add_entities, _build_new)


# ── Binary sensor entity classes ──────────────────────────────────────────────


class QuiltIDUBinarySensor(QuiltIDUEntity, BinarySensorEntity):
    """Binary sensor entity for a Quilt indoor unit (QSM)."""

    entity_description: IDUBinarySensorDescription

    def __init__(
        self,
        coordinator: QuiltCoordinator,
        idu_id: str,
        description: IDUBinarySensorDescription,
    ) -> None:
        """Initialize the IDU binary sensor entity."""
        super().__init__(coordinator, idu_id)
        self.entity_description = description
        self._attr_unique_id: str = f"quilt_idu_{idu_id}_{description.key}"

    @override
    def _model_available(self, idu: IndoorUnit) -> bool:
        return self.entity_description.available_fn(idu)

    @property
    @override
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self._idu)


class QuiltControllerBinarySensor(QuiltControllerEntity, BinarySensorEntity):
    """Binary sensor entity for a Quilt Controller (Dial)."""

    entity_description: ControllerBinarySensorDescription

    def __init__(
        self,
        coordinator: QuiltCoordinator,
        ctrl_id: str,
        description: ControllerBinarySensorDescription,
    ) -> None:
        """Initialize the controller binary sensor entity."""
        super().__init__(coordinator, ctrl_id)
        self.entity_description = description
        self._attr_unique_id: str = f"quilt_ctrl_{ctrl_id}_{description.key}"

    @override
    def _model_available(self, ctrl: Controller) -> bool:
        return self.entity_description.available_fn(ctrl)

    @property
    @override
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self._ctrl)
