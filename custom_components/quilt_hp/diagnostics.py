"""Diagnostics support for the Quilt Heat Pump integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import QuiltCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    Sensitive data (email address, tokens) is redacted.  The output is
    intended for attaching to bug reports.
    """
    coordinator: QuiltCoordinator = entry.runtime_data
    data = coordinator.data

    spaces_info: list[dict[str, Any]] = []
    idu_info: list[dict[str, Any]] = []
    odu_info: list[dict[str, Any]] = []
    ctrl_info: list[dict[str, Any]] = []

    for space in data.spaces:
        spaces_info.append(
            {
                "id": space.id[:8] + "…",
                "is_room": space.is_room,
                "hvac_mode": str(space.controls.hvac_mode),
                "hvac_state": str(space.state.hvac_state),
            }
        )

    for idu in data.indoor_units:
        idu_info.append(
            {
                "id": idu.id[:8] + "…",
                "is_online": idu.is_online,
                "fan_speed": str(idu.controls.fan_speed),
                "has_qsm": idu.qsm_id is not None,
            }
        )

    for odu in data.outdoor_units:
        odu_info.append(
            {
                "id": odu.id[:8] + "…",
                "model_sku": odu.model_sku,
                "firmware_version": odu.firmware_version,
                "has_performance_data": odu.performance_data is not None,
            }
        )

    for ctrl in data.controllers:
        ctrl_info.append(
            {
                "id": ctrl.id[:8] + "…",
                "is_online": ctrl.is_online,
                "firmware_version": ctrl.firmware_version,
            }
        )

    return {
        "entry": {
            "version": entry.version,
            "domain": entry.domain,
        },
        "coordinator": {
            "is_streaming": coordinator.is_streaming,
            "last_update_success": coordinator.last_update_success,
            "stream_error_count": coordinator.stream_error_count,
        },
        "spaces": spaces_info,
        "indoor_units": idu_info,
        "outdoor_units": odu_info,
        "controllers": ctrl_info,
    }
