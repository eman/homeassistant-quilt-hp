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
