"""Device and entity registry helpers for the Quilt Heat Pump integration."""

from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er

from quilt_hp.models.system import SystemSnapshot

from .const import DOMAIN

# Device identifier prefix per stream object kind (``NotifierStream.on_delete``).
# They match the ``*_device_info`` builders in entity.py. Spaces and QSMs have no
# device of their own: space entities sit on the room's indoor unit, and the QSM
# is part of its indoor unit.
DEVICE_PREFIX_BY_KIND: dict[str, str] = {
    "indoor_unit": "i_",
    "outdoor_unit": "u_",
    "controller": "c_",
    "remote_sensor": "rs_",
    "controller_remote_sensor": "crs_",
}


def device_identifiers(snapshot: SystemSnapshot) -> set[tuple[str, str]]:
    """Return the device identifiers of every device *snapshot* backs."""
    return {
        *((DOMAIN, f"i_{idu.id}") for idu in snapshot.indoor_units),
        *((DOMAIN, f"u_{odu.id}") for odu in snapshot.outdoor_units),
        *((DOMAIN, f"c_{ctrl.id}") for ctrl in snapshot.controllers),
        *((DOMAIN, f"rs_{rs.id}") for rs in snapshot.remote_sensors),
        *((DOMAIN, f"crs_{crs.id}") for crs in snapshot.controller_remote_sensors),
        *((DOMAIN, f"loc_{loc.id}") for loc in snapshot.locations),
    }


def _is_space_entity(unique_id: str) -> bool:
    """Return True for entities keyed by a space (the climate and space sensors)."""
    return unique_id.startswith("quilt_space_")


@callback
def async_remove_stale_devices(
    hass: HomeAssistant, entry_id: str, snapshot: SystemSnapshot
) -> None:
    """Remove registry devices and space entities *snapshot* no longer has.

    Space entities live on the room's indoor-unit device, so a deleted room
    whose indoor unit remains would otherwise keep an orphaned climate entity.
    """
    valid = device_identifiers(snapshot)
    device_registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(device_registry, entry_id):
        if not device.identifiers & valid:
            _ = device_registry.async_update_device(
                device.id, remove_config_entry_id=entry_id
            )

    space_ids = [space.id for space in snapshot.spaces]
    entity_registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(entity_registry, entry_id):
        if _is_space_entity(entity.unique_id) and not any(
            space_id in entity.unique_id for space_id in space_ids
        ):
            entity_registry.async_remove(entity.entity_id)


@callback
def async_remove_deleted(
    hass: HomeAssistant, entry_id: str, kind: str, object_id: str
) -> None:
    """Remove the device (or space entities) of one object deleted from Quilt."""
    if kind == "space":
        entity_registry = er.async_get(hass)
        for entity in er.async_entries_for_config_entry(entity_registry, entry_id):
            if _is_space_entity(entity.unique_id) and object_id in entity.unique_id:
                entity_registry.async_remove(entity.entity_id)
        return
    prefix = DEVICE_PREFIX_BY_KIND.get(kind)
    if prefix is None:
        return
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, f"{prefix}{object_id}"), entry_id
    )
    if device is not None:
        _ = device_registry.async_update_device(
            device.id, remove_config_entry_id=entry_id
        )
