"""Home Assistant integration entry point for Quilt Heat Pump."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from quilt_hp.exceptions import QuiltAuthError
from quilt_hp.models.system import SystemSnapshot

from .const import (
    CONF_EMAIL,
    CONF_SYSTEM_ID,
    DOMAIN,
    INITIAL_FETCH_TIMEOUT_S,
    PLATFORMS,
)
from .coordinator import QuiltCoordinator
from .token_store import HATokenStore

_LOGGER = logging.getLogger(__name__)

# Typed config entry — avoids hass.data lookups in all platform setups.
type QuiltConfigEntry = ConfigEntry[QuiltCoordinator]


async def async_migrate_entry(_hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entries to the current version.

    V1 is the only version that has ever existed. This stub is here so that
    future migrations have a well-defined starting point.
    """
    _LOGGER.debug(
        "Migrating Quilt config entry from version %s to %s",
        entry.version,
        1,
    )
    if entry.version == 1:
        return True

    _LOGGER.error(
        "Cannot migrate Quilt config entry from unknown version %s", entry.version
    )
    return False


async def async_setup_entry(hass: HomeAssistant, entry: QuiltConfigEntry) -> bool:
    """Set up Quilt Heat Pump from a config entry."""
    email: str = entry.data[CONF_EMAIL]
    system_id: str | None = entry.data.get(CONF_SYSTEM_ID)
    coordinator = QuiltCoordinator(hass, entry, email, system_id=system_id)

    try:
        async with asyncio.timeout(INITIAL_FETCH_TIMEOUT_S):
            await coordinator.async_setup()
    except ConfigEntryAuthFailed:
        raise
    except QuiltAuthError as err:
        # Cached tokens rejected and no OTP callback available — the user
        # must re-authenticate interactively.
        raise ConfigEntryAuthFailed(f"Quilt authentication failed: {err}") from err
    except TimeoutError as err:
        raise ConfigEntryNotReady("Timed out fetching initial Quilt snapshot") from err
    except Exception as err:
        raise ConfigEntryNotReady(f"Quilt setup failed: {err}") from err

    entry.runtime_data = coordinator
    # Register cleanup before forwarding platforms so a failing platform
    # setup still closes the stream and gRPC channel.
    entry.async_on_unload(coordinator.async_shutdown)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _async_cleanup_stale_devices(hass, entry, coordinator.data)

    async def _async_reload_on_options_update(
        hass: HomeAssistant, entry: QuiltConfigEntry
    ) -> None:
        await hass.config_entries.async_reload(entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options_update))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: QuiltConfigEntry) -> bool:
    """Unload a Quilt Heat Pump config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


@callback
def _async_cleanup_stale_devices(
    hass: HomeAssistant, entry: QuiltConfigEntry, snapshot: SystemSnapshot
) -> None:
    """Remove registry devices that no longer exist in the Quilt account.

    Identifier prefixes match the ``*_device_info`` builders in entity.py.
    """
    valid_identifiers: set[tuple[str, str]] = {
        *((DOMAIN, f"i_{idu.id}") for idu in snapshot.indoor_units),
        *((DOMAIN, f"u_{odu.id}") for odu in snapshot.outdoor_units),
        *((DOMAIN, f"c_{ctrl.id}") for ctrl in snapshot.controllers),
        *((DOMAIN, f"rs_{rs.id}") for rs in snapshot.remote_sensors),
        *((DOMAIN, f"crs_{crs.id}") for crs in snapshot.controller_remote_sensors),
        *((DOMAIN, f"loc_{loc.id}") for loc in snapshot.locations),
    }
    device_registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if not device.identifiers & valid_identifiers:
            device_registry.async_update_device(
                device.id, remove_config_entry_id=entry.entry_id
            )


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the account's cached tokens when the entry is removed.

    Tokens are only removed when no other config entry uses the same email
    (multiple homes on one account share credentials).
    """
    email: str | None = entry.data.get(CONF_EMAIL)
    if not email:
        return
    for other in hass.config_entries.async_entries(DOMAIN):
        if other.entry_id != entry.entry_id and other.data.get(CONF_EMAIL) == email:
            return
    await HATokenStore(hass).delete(email)
