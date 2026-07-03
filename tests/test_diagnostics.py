"""Tests for the diagnostics platform."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.quilt_hp.diagnostics import async_get_config_entry_diagnostics

from .conftest import (
    make_controller,
    make_idu,
    make_mock_coordinator,
    make_odu,
    make_snapshot,
    make_space,
)


async def test_async_get_config_entry_diagnostics(hass) -> None:
    """Test getting diagnostics data."""
    space = make_space()
    idu = make_idu()
    odu = make_odu()
    controller = make_controller()

    snapshot = make_snapshot(
        spaces=[space],
        indoor_units=[idu],
        outdoor_units=[odu],
        controllers=[controller],
    )
    coordinator = make_mock_coordinator(hass, snapshot)

    entry = MagicMock()
    entry.runtime_data = coordinator
    entry.entry_id = "test_entry"
    entry.version = 1
    entry.domain = "quilt_hp"

    # Set up hass.data for diagnostics
    hass.data = {"quilt_hp": {"test_entry": coordinator}}

    diag_data = await async_get_config_entry_diagnostics(hass, entry)

    # Check structure
    assert "entry" in diag_data
    assert "coordinator" in diag_data
    assert "spaces" in diag_data
    assert "indoor_units" in diag_data
    assert "outdoor_units" in diag_data
    assert "controllers" in diag_data

    # Check counts
    assert len(diag_data["spaces"]) == 1
    assert len(diag_data["indoor_units"]) == 1
    assert len(diag_data["outdoor_units"]) == 1
    assert len(diag_data["controllers"]) == 1

    # Check entry data
    assert diag_data["entry"]["version"] == 1
    assert diag_data["entry"]["domain"] == "quilt_hp"


async def test_diagnostics_empty(hass) -> None:
    """Test diagnostics with minimal data."""
    # Create an empty snapshot - conftest make_snapshot adds defaults
    from custom_components.quilt_hp.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    from .conftest import make_mock_coordinator

    snapshot = MagicMock()
    snapshot.spaces = []
    snapshot.indoor_units = []
    snapshot.outdoor_units = []
    snapshot.controllers = []
    snapshot.quilt_smart_modules = []

    coordinator = make_mock_coordinator(hass, snapshot)
    coordinator.data = snapshot  # Ensure data is set

    entry = MagicMock()
    entry.runtime_data = coordinator
    entry.entry_id = "test_entry"
    entry.version = 1
    entry.domain = "quilt_hp"

    hass.data = {"quilt_hp": {"test_entry": coordinator}}

    diag_data = await async_get_config_entry_diagnostics(hass, entry)

    assert diag_data["spaces"] == []
    assert diag_data["indoor_units"] == []
    assert diag_data["outdoor_units"] == []
    assert diag_data["controllers"] == []


async def test_diagnostics_reports_stream_death_count(hass) -> None:
    """Diagnostics must expose the renamed stream_death_count key."""
    coordinator = make_mock_coordinator(hass, make_snapshot())
    coordinator.stream_death_count = 3
    coordinator.is_streaming = True

    entry = MagicMock()
    entry.runtime_data = coordinator
    entry.version = 1
    entry.domain = "quilt_hp"

    diag_data = await async_get_config_entry_diagnostics(hass, entry)

    assert diag_data["coordinator"]["stream_death_count"] == 3
    assert diag_data["coordinator"]["is_streaming"] is True
    assert diag_data["coordinator"]["last_update_success"] is True
    assert "stream_error_count" not in diag_data["coordinator"]
