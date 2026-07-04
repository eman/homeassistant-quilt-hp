"""Climate platform for Quilt Heat Pump — one entity per Space."""

from __future__ import annotations

from dataclasses import replace
import logging
from typing import TYPE_CHECKING, Any, override

from homeassistant.components.climate import (
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    PRESET_AWAY,
    PRESET_NONE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from quilt_hp.models.comfort import ComfortSetting
from quilt_hp.models.enums import (
    ComfortSettingType,
    HVACMode as QHVACMode,
    HVACState as QHVACState,
)
from quilt_hp.models.space import Space

from .const import DOMAIN
from .coordinator import QuiltCoordinator
from .entity import QuiltEntity, async_setup_dynamic_entities, idu_device_info
from .utils import normalize_float

if TYPE_CHECKING:
    from . import QuiltConfigEntry

_LOGGER = logging.getLogger(__name__)

# Limit concurrent updates to avoid overwhelming the device
PARALLEL_UPDATES = 1

_PRESET_MODES: list[str] = [PRESET_NONE, PRESET_AWAY]

# ── Mode maps ─────────────────────────────────────────────────────────────────

_Q_TO_HA: dict[QHVACMode, HVACMode] = {
    QHVACMode.STANDBY: HVACMode.OFF,
    QHVACMode.COOL: HVACMode.COOL,
    QHVACMode.HEAT: HVACMode.HEAT,
    QHVACMode.AUTO: HVACMode.HEAT_COOL,
    QHVACMode.FAN: HVACMode.FAN_ONLY,
    QHVACMode.DRY: HVACMode.DRY,
    QHVACMode.FALLBACK_AUTO: HVACMode.HEAT_COOL,
    QHVACMode.FALLBACK_OFF: HVACMode.OFF,
}

_HA_TO_Q: dict[HVACMode, QHVACMode] = {
    HVACMode.OFF: QHVACMode.STANDBY,
    HVACMode.COOL: QHVACMode.COOL,
    HVACMode.HEAT: QHVACMode.HEAT,
    HVACMode.HEAT_COOL: QHVACMode.AUTO,
    HVACMode.FAN_ONLY: QHVACMode.FAN,
    HVACMode.DRY: QHVACMode.DRY,
}

_Q_STATE_TO_HA_ACTION: dict[QHVACState, HVACAction] = {
    QHVACState.STANDBY: HVACAction.IDLE,
    QHVACState.COOL: HVACAction.COOLING,
    QHVACState.HEAT: HVACAction.HEATING,
    QHVACState.DRIFT: HVACAction.IDLE,
    QHVACState.FAN: HVACAction.FAN,
    QHVACState.COOL_DEFERRED: HVACAction.COOLING,
    QHVACState.HEAT_DEFERRED: HVACAction.HEATING,
    QHVACState.FAN_DEFERRED: HVACAction.FAN,
    QHVACState.COOL_PREPARING: HVACAction.COOLING,
    QHVACState.HEAT_PREPARING: HVACAction.HEATING,
    QHVACState.DRY: HVACAction.DRYING,
    QHVACState.DRY_DEFERRED: HVACAction.DRYING,
    QHVACState.DRY_PREPARING: HVACAction.DRYING,
}


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: QuiltConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up climate entities from a config entry."""
    coordinator = entry.runtime_data

    def _build_new(known: set[str]) -> list[tuple[str, QuiltClimateEntity]]:
        first_idu = coordinator.first_idu_id_by_space_id
        return [
            (space.id, QuiltClimateEntity(coordinator, space.id, first_idu[space.id]))
            for space in coordinator.data.spaces
            if space.is_room and space.id in first_idu and space.id not in known
        ]

    async_setup_dynamic_entities(entry, coordinator, async_add_entities, _build_new)


class QuiltClimateEntity(QuiltEntity, ClimateEntity):
    """Climate entity representing a Quilt space (room)."""

    _attr_temperature_unit: UnitOfTemperature = UnitOfTemperature.CELSIUS
    _attr_supported_features: ClimateEntityFeature = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    # Only Away is exposed as an HA preset: it is a real user-facing Quilt
    # state (set by occupancy automation or the app), unlike the other comfort
    # settings which are schedule-internal.
    _attr_preset_modes: list[str] = _PRESET_MODES
    # Quilt supports setpoints from 10 °C (safety heating floor) to 32 °C.
    _attr_min_temp: float = 10.0
    _attr_max_temp: float = 32.0
    _attr_target_temperature_step: float = 0.5
    _attr_name: str | None = None  # use device name as entity name

    def __init__(
        self, coordinator: QuiltCoordinator, space_id: str, idu_id: str
    ) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator)
        self._space_id: str = space_id
        self._idu_id: str = idu_id
        self._attr_unique_id: str = f"quilt_space_climate_{space_id}"
        self._attr_hvac_modes: list[HVACMode] = list(_HA_TO_Q.keys())

    @property
    def _space(self) -> Space:
        return self.coordinator.spaces_by_id[self._space_id]

    @property
    @override
    def available(self) -> bool:
        idu = self.coordinator.idu_by_id.get(self._idu_id)
        return (
            super().available
            and self._space_id in self.coordinator.spaces_by_id
            and idu is not None
            and idu.is_online
        )

    @property
    @override
    def device_info(self) -> DeviceInfo:
        idu = self.coordinator.idu_by_id[self._idu_id]
        space = self.coordinator.spaces_by_id.get(self._space_id)
        return idu_device_info(idu, space)

    @property
    @override
    def hvac_mode(self) -> HVACMode:
        return _Q_TO_HA.get(self._space.controls.hvac_mode, HVACMode.OFF)

    @property
    @override
    def hvac_action(self) -> HVACAction | None:
        return _Q_STATE_TO_HA_ACTION.get(self._space.state.hvac_state)

    @property
    def _is_explicit_off_mode(self) -> bool:
        return self._space.controls.hvac_mode in (
            QHVACMode.STANDBY,
            QHVACMode.FALLBACK_OFF,
        )

    @property
    def _supports_setpoint_display(self) -> bool:
        return self.hvac_mode in (HVACMode.HEAT, HVACMode.COOL, HVACMode.HEAT_COOL)

    @property
    def _active_comfort_setting(self) -> ComfortSetting | None:
        """Return the active ComfortSetting object, or None if unavailable."""
        cs_id = self._space.controls.comfort_setting_id_or_none
        if cs_id is None:
            return None
        return self.coordinator.cs_by_id.get(cs_id)

    @property
    def _effective_setpoints(self) -> tuple[float | None, float | None]:
        """Return (heat_setpoint_c, cool_setpoint_c) with sentinel handling.

        When the API returns placeholder setpoints (e.g. 8 °C / 40 °C) to
        indicate "not configured", we prefer the active comfort setting's
        setpoints instead.  If those are also unavailable we fall back to the
        current state setpoint.
        """
        heat = normalize_float(self._space.controls.heating_setpoint_c)
        cool = normalize_float(self._space.controls.cooling_setpoint_c)

        if not self._space.controls.has_standby_sentinel_setpoints:
            return heat, cool

        comfort_setting = self._active_comfort_setting
        if (
            comfort_setting is not None
            and not comfort_setting.has_placeholder_setpoints
        ):
            heat = normalize_float(comfort_setting.heating_setpoint_c)
            cool = normalize_float(comfort_setting.cooling_setpoint_c)
            return heat, cool

        # Fall back to the current state setpoint when the comfort setting
        # is also a placeholder.
        if not self._space.state.has_missing_setpoint:
            setpoint = normalize_float(self._space.state.setpoint_c)
            if setpoint is not None:
                if self._space.controls.hvac_mode == QHVACMode.HEAT:
                    heat = setpoint
                elif self._space.controls.hvac_mode == QHVACMode.COOL:
                    cool = setpoint
        return heat, cool

    @property
    @override
    def current_temperature(self) -> float | None:
        if self._space.state.has_missing_ambient_temperature:
            return None
        return normalize_float(self._space.state.ambient_temperature_c)

    @property
    @override
    def target_temperature(self) -> float | None:
        if self._is_explicit_off_mode or not self._supports_setpoint_display:
            return None
        heat, cool = self._effective_setpoints
        mode = self._space.controls.hvac_mode
        if mode == QHVACMode.COOL:
            return cool
        if mode == QHVACMode.HEAT:
            return heat
        return None

    @property
    @override
    def target_temperature_high(self) -> float | None:
        # Range setpoints apply only in Heat/Cool (auto); single-setpoint
        # Heat and Cool modes use target_temperature instead.
        if self.hvac_mode != HVACMode.HEAT_COOL:
            return None
        _, cool = self._effective_setpoints
        return cool

    @property
    @override
    def target_temperature_low(self) -> float | None:
        if self.hvac_mode != HVACMode.HEAT_COOL:
            return None
        heat, _ = self._effective_setpoints
        return heat

    @override
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        quilt_mode = _HA_TO_Q[hvac_mode]
        await self.coordinator.async_set_space(self._space, mode=quilt_mode)
        await self._async_refresh_if_not_streaming()

    @override
    async def async_set_temperature(self, **kwargs: Any) -> None:
        heat_sp = kwargs.get(ATTR_TARGET_TEMP_LOW)
        cool_sp = kwargs.get(ATTR_TARGET_TEMP_HIGH)

        if (temp := kwargs.get(ATTR_TEMPERATURE)) is not None:
            if self.hvac_mode == HVACMode.HEAT:
                heat_sp = temp
            else:
                cool_sp = temp

        await self.coordinator.async_set_space(
            self._space,
            heat_setpoint_c=heat_sp,
            cool_setpoint_c=cool_sp,
        )
        await self._async_refresh_if_not_streaming()

    # ------------------------------------------------------------------
    # Away preset — Quilt's occupancy "away" state, toggleable from HA
    # ------------------------------------------------------------------

    def _comfort_setting_of_type(
        self, cs_type: ComfortSettingType
    ) -> ComfortSetting | None:
        """Return this space's comfort setting of *cs_type*, if any."""
        return next(
            (
                cs
                for cs in self.coordinator.cs_by_space_id.get(self._space_id, [])
                if cs.type is cs_type
            ),
            None,
        )

    @property
    @override
    def preset_mode(self) -> str:
        """Return PRESET_AWAY when the room is in away mode, else PRESET_NONE."""
        return PRESET_AWAY if self._space.is_away else PRESET_NONE

    @override
    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Enter or leave away mode by activating the matching comfort setting.

        Selecting *away* activates the room's Away comfort setting (the same
        state occupancy automation or the Quilt app sets). Selecting *none*
        while away restores the room's Active (normal occupied) comfort
        setting; Quilt may re-enter away automatically if the room stays
        unoccupied.
        """
        if preset_mode == PRESET_AWAY:
            target = self._comfort_setting_of_type(ComfortSettingType.AWAY)
        elif preset_mode == PRESET_NONE:
            if not self._space.is_away:
                return
            target = self._comfort_setting_of_type(ComfortSettingType.ACTIVE)
        else:
            raise ServiceValidationError(
                f"Unsupported preset {preset_mode!r} for space {self._space_id}",
                translation_domain=DOMAIN,
                translation_key="unknown_preset",
                translation_placeholders={"preset": preset_mode},
            )

        if target is None:
            raise ServiceValidationError(
                f"No comfort setting available to apply preset {preset_mode!r}",
                translation_domain=DOMAIN,
                translation_key="unknown_preset",
                translation_placeholders={"preset": preset_mode},
            )

        # set_space preserves the space's comfort_setting_id, so point it at the
        # target comfort setting and send that setting's mode and setpoints.
        target_space = replace(
            self._space,
            controls=replace(self._space.controls, comfort_setting_id=target.id),
        )
        await self.coordinator.async_set_space(
            target_space,
            mode=target.hvac_mode,
            heat_setpoint_c=target.heating_setpoint_c,
            cool_setpoint_c=target.cooling_setpoint_c,
        )
        await self._async_refresh_if_not_streaming()
