"""Binary sensor platform for Quilt Heat Pump.

Provides binary sensor entities for:
- QSM/IDU: presence (realtime, OR of both radar channels), occupancy
  (derived auto-away decision), raw radar channels (diagnostic), online
- Controller (Dial): online

Presence data has three tiers — see eman/homeassistant-quilt-hp#12:
- ``sensor0_presence``/``sensor1_presence`` are the two detection channels
  of the IDU's single mm-wave radar. Which channel is which is unconfirmed
  and they move in lockstep in practice; the vendor app never distinguishes
  them, it ORs both into one room-presence value.
- The OR of the channels is the realtime "someone is in the room" signal
  (flips within seconds) — our ``presence`` entity.
- ``effective_occupancy_state`` is the server's debounced auto-away
  decision (~3 min of sustained presence to set, ~20 min of absence to
  clear, per space timeout settings) — our ``occupied`` entity.
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


def _radar_channel_presence(presence_val: Presence | None) -> bool | None:
    """Map a raw radar channel presence enum value to bool or None.

    Returns True for DETECTED, False for UNDETECTED, and None for
    UNSPECIFIED (or missing presence data).
    """
    if presence_val == Presence.DETECTED:
        return True
    if presence_val == Presence.UNDETECTED:
        return False
    return None


IDU_BINARY_SENSOR_DESCRIPTIONS: tuple[IDUBinarySensorDescription, ...] = (
    IDUBinarySensorDescription(
        # Key kept as "presence" so existing entities/history carry over;
        # the value is now the OR of both channels (previously channel 1
        # only — identical in practice, the channels move in lockstep).
        key="presence",
        translation_key="presence",
        device_class=BinarySensorDeviceClass.OCCUPANCY,
        value_fn=lambda idu: idu.presence_detected,
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
        # Key kept as "motion" so existing entities/history carry over.
        # No MOTION device class: there is no evidence this channel is
        # motion-specific (see module docstring).
        key="motion",
        translation_key="radar_channel_0",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda idu: (
            _radar_channel_presence(idu.presence.sensor0_presence)
            if idu.presence is not None
            else None
        ),
    ),
    IDUBinarySensorDescription(
        key="radar_1",
        translation_key="radar_channel_1",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda idu: (
            _radar_channel_presence(idu.presence.sensor1_presence)
            if idu.presence is not None
            else None
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
