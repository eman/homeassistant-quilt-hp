"""Tests for the __init__ module."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
import pytest
from quilt_hp.exceptions import QuiltAuthError

from custom_components.quilt_hp import (
    async_remove_entry,
    async_setup_entry,
    async_unload_entry,
)

from .conftest import make_snapshot


def _make_entry(entry_id: str = "test_entry") -> MagicMock:
    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = entry_id
    entry.data = {"email": "test@example.com", "system_id": "test_system"}
    entry.async_on_unload = MagicMock(return_value=None)
    entry.add_update_listener = MagicMock(return_value=None)
    return entry


async def test_async_setup_entry_success(hass: HomeAssistant) -> None:
    """Test successful setup of a config entry."""
    entry = _make_entry()

    with patch("custom_components.quilt_hp.QuiltCoordinator") as mock_coord_class:
        mock_coordinator = MagicMock()
        mock_coordinator.async_setup = AsyncMock()
        mock_coordinator.async_shutdown = AsyncMock()
        mock_coordinator.data = make_snapshot()
        mock_coord_class.return_value = mock_coordinator

        with patch.object(
            hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
        ):
            result = await async_setup_entry(hass, entry)

    assert result is True
    mock_coordinator.async_setup.assert_awaited_once()
    assert entry.runtime_data is mock_coordinator
    # Shutdown cleanup must be registered before platforms are forwarded.
    entry.async_on_unload.assert_any_call(mock_coordinator.async_shutdown)


async def test_async_setup_entry_timeout(hass: HomeAssistant) -> None:
    """Test setup failure due to timeout."""
    entry = _make_entry()

    async def slow_setup():
        await asyncio.sleep(100)

    with (
        patch("custom_components.quilt_hp.QuiltCoordinator") as mock_coord_class,
        patch("custom_components.quilt_hp.INITIAL_FETCH_TIMEOUT_S", 0.01),
    ):
        mock_coordinator = MagicMock()
        mock_coordinator.async_setup = AsyncMock(side_effect=slow_setup)
        mock_coord_class.return_value = mock_coordinator

        with pytest.raises(ConfigEntryNotReady, match="Timed out"):
            await async_setup_entry(hass, entry)


async def test_async_setup_entry_failure(hass: HomeAssistant) -> None:
    """Generic setup failures map to ConfigEntryNotReady (retry later)."""
    entry = _make_entry()

    with patch("custom_components.quilt_hp.QuiltCoordinator") as mock_coord_class:
        mock_coordinator = MagicMock()
        mock_coordinator.async_setup = AsyncMock(
            side_effect=Exception("Connection failed")
        )
        mock_coord_class.return_value = mock_coordinator

        with pytest.raises(ConfigEntryNotReady, match="Quilt setup failed"):
            await async_setup_entry(hass, entry)


async def test_async_setup_entry_quilt_auth_error_maps_to_auth_failed(
    hass: HomeAssistant,
) -> None:
    """QuiltAuthError must trigger reauth, not endless retries."""
    entry = _make_entry()

    with patch("custom_components.quilt_hp.QuiltCoordinator") as mock_coord_class:
        mock_coordinator = MagicMock()
        mock_coordinator.async_setup = AsyncMock(
            side_effect=QuiltAuthError("tokens rejected")
        )
        mock_coord_class.return_value = mock_coordinator

        with pytest.raises(ConfigEntryAuthFailed):
            await async_setup_entry(hass, entry)


async def test_async_setup_entry_config_entry_auth_failed_passthrough(
    hass: HomeAssistant,
) -> None:
    """ConfigEntryAuthFailed from the coordinator must propagate unwrapped."""
    entry = _make_entry()

    with patch("custom_components.quilt_hp.QuiltCoordinator") as mock_coord_class:
        mock_coordinator = MagicMock()
        mock_coordinator.async_setup = AsyncMock(
            side_effect=ConfigEntryAuthFailed("refresh token expired")
        )
        mock_coord_class.return_value = mock_coordinator

        with pytest.raises(ConfigEntryAuthFailed) as excinfo:
            await async_setup_entry(hass, entry)
    assert not isinstance(excinfo.value, ConfigEntryNotReady)


async def test_async_unload_entry(hass: HomeAssistant) -> None:
    """Test unloading a config entry."""
    entry = _make_entry()

    with patch.object(
        hass.config_entries, "async_unload_platforms", new=AsyncMock(return_value=True)
    ):
        result = await async_unload_entry(hass, entry)
        assert result is True


async def test_cleanup_removed_entities(hass: HomeAssistant) -> None:
    """Obsolete fan entity and RPM sensors are removed; current entities kept."""
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.quilt_hp import _async_cleanup_removed_entities
    from custom_components.quilt_hp.const import DOMAIN

    entry = MockConfigEntry(domain=DOMAIN, data={"email": "a@b.com"})
    entry.add_to_hass(hass)
    reg = er.async_get(hass)

    fan = reg.async_get_or_create(
        "fan", DOMAIN, "quilt_idu_fan_idu-1", config_entry=entry
    )
    rpm = reg.async_get_or_create(
        "sensor", DOMAIN, "quilt_idu_idu-1_fan_speed_rpm", config_entry=entry
    )
    setpoint = reg.async_get_or_create(
        "sensor", DOMAIN, "quilt_idu_idu-1_fan_speed_setpoint_rpm", config_entry=entry
    )
    speed_select = reg.async_get_or_create(
        "select", DOMAIN, "quilt_idu_fan_speed_idu-1", config_entry=entry
    )
    humidity = reg.async_get_or_create(
        "sensor", DOMAIN, "quilt_idu_idu-1_ambient_humidity", config_entry=entry
    )

    _async_cleanup_removed_entities(hass, entry)

    assert reg.async_get(fan.entity_id) is None
    assert reg.async_get(rpm.entity_id) is None
    assert reg.async_get(setpoint.entity_id) is None
    assert reg.async_get(speed_select.entity_id) is not None
    assert reg.async_get(humidity.entity_id) is not None


async def test_async_migrate_entry_v1(hass: HomeAssistant) -> None:
    """Test migration for v1 (no-op)."""
    from custom_components.quilt_hp import async_migrate_entry

    entry = MagicMock(spec=ConfigEntry)
    entry.version = 1

    result = await async_migrate_entry(hass, entry)
    assert result is True


async def test_async_migrate_entry_unknown_version(hass: HomeAssistant) -> None:
    """Test migration failure for unknown version."""
    from custom_components.quilt_hp import async_migrate_entry

    entry = MagicMock(spec=ConfigEntry)
    entry.version = 999

    result = await async_migrate_entry(hass, entry)
    assert result is False


# ── async_remove_entry ────────────────────────────────────────────────────────


async def test_async_remove_entry_deletes_tokens_for_last_entry(
    hass: HomeAssistant,
) -> None:
    """Removing the last entry for an email must delete its cached tokens."""
    entry = _make_entry()

    with (
        patch.object(hass.config_entries, "async_entries", return_value=[entry]),
        patch("custom_components.quilt_hp.HATokenStore") as mock_store_class,
    ):
        mock_store = mock_store_class.return_value
        mock_store.delete = AsyncMock()
        await async_remove_entry(hass, entry)

    mock_store.delete.assert_awaited_once_with("test@example.com")


async def test_async_remove_entry_keeps_tokens_when_email_shared(
    hass: HomeAssistant,
) -> None:
    """Tokens must survive when another entry uses the same account."""
    entry = _make_entry("entry-1")
    other = _make_entry("entry-2")

    with (
        patch.object(hass.config_entries, "async_entries", return_value=[entry, other]),
        patch("custom_components.quilt_hp.HATokenStore") as mock_store_class,
    ):
        mock_store = mock_store_class.return_value
        mock_store.delete = AsyncMock()
        await async_remove_entry(hass, entry)

    mock_store.delete.assert_not_awaited()


async def test_async_remove_entry_no_email(hass: HomeAssistant) -> None:
    entry = _make_entry()
    entry.data = {}

    with patch("custom_components.quilt_hp.HATokenStore") as mock_store_class:
        mock_store = mock_store_class.return_value
        mock_store.delete = AsyncMock()
        await async_remove_entry(hass, entry)

    mock_store.delete.assert_not_awaited()


# ── Full setup through Home Assistant ─────────────────────────────────────────


def _dial_snapshot():
    from quilt_hp.models.enums import RemoteSensorControlMode

    from .conftest import make_controller

    ctrl = make_controller()
    ctrl.remote_sensor_mode = RemoteSensorControlMode.ENABLED
    return make_snapshot(controllers=[ctrl])


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_setup_loads_every_platform_and_unloads(
    hass: HomeAssistant, mock_client
) -> None:
    """Set up, reload and unload the entry with the real platforms."""
    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.quilt_hp.const import DOMAIN

    client, _stream = mock_client
    client.get_snapshot = AsyncMock(return_value=_dial_snapshot())
    entry = MockConfigEntry(
        domain=DOMAIN, data={"email": "a@b.com", "system_id": "sys-001"}
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    registry = er.async_get(hass)
    start = registry.async_get_entity_id(
        "button", DOMAIN, "quilt_idu_idu-001_start_self_test"
    )
    assert start is not None
    assert registry.async_get(start).disabled_by is er.RegistryEntryDisabler.INTEGRATION

    switch_id = registry.async_get_entity_id(
        "switch", DOMAIN, "quilt_ctrl_ctrl-001_use_dial_temperature"
    )
    assert switch_id is not None
    state = hass.states.get(switch_id)
    assert state is not None
    assert state.state == "on"
    assert state.attributes["friendly_name"].endswith("Use Dial temperature")

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_remove_config_entry_device_only_when_gone(
    hass: HomeAssistant, mock_client
) -> None:
    """A device can be deleted by hand only once Quilt no longer has it."""
    from homeassistant.helpers import device_registry as dr
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.quilt_hp import async_remove_config_entry_device
    from custom_components.quilt_hp.const import DOMAIN

    client, _stream = mock_client
    client.get_snapshot = AsyncMock(return_value=_dial_snapshot())
    entry = MockConfigEntry(
        domain=DOMAIN, data={"email": "a@b.com", "system_id": "sys-001"}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = dr.async_get(hass)
    present = registry.async_get_device_by_identifier(
        (DOMAIN, "c_ctrl-001"), entry.entry_id
    )
    assert present is not None
    gone = registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "c_ctrl-gone")}
    )

    assert not await async_remove_config_entry_device(hass, entry, present)
    assert await async_remove_config_entry_device(hass, entry, gone)

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_remove_stale_devices_and_room_entities(hass: HomeAssistant) -> None:
    """Setup cleanup drops devices and room entities Quilt no longer has."""
    from homeassistant.helpers import device_registry as dr, entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.quilt_hp.const import DOMAIN
    from custom_components.quilt_hp.registry import async_remove_stale_devices

    entry = MockConfigEntry(domain=DOMAIN, data={"email": "a@b.com"})
    entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    kept = devices.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "i_idu-001")}
    )
    stale = devices.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "c_ctrl-gone")}
    )
    room = entities.async_get_or_create(
        "climate", DOMAIN, "quilt_space_climate_space-001", config_entry=entry
    )
    old_room = entities.async_get_or_create(
        "climate", DOMAIN, "quilt_space_climate_space-gone", config_entry=entry
    )
    old_room_sensor = entities.async_get_or_create(
        "sensor", DOMAIN, "quilt_space_space-gone_energy_today", config_entry=entry
    )

    async_remove_stale_devices(hass, entry.entry_id, make_snapshot())

    assert devices.async_get(kept.id) is not None
    assert devices.async_get(stale.id) is None
    assert entities.async_get(room.entity_id) is not None
    assert entities.async_get(old_room.entity_id) is None
    assert entities.async_get(old_room_sensor.entity_id) is None
