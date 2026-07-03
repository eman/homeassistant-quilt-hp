"""DataUpdateCoordinator for the Quilt Heat Pump integration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
from datetime import datetime, timedelta
import logging
from typing import Any, override

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.issue_registry import (
    IssueSeverity,
    async_create_issue,
    async_delete_issue,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from quilt_hp import NotifierStream, QuiltClient
from quilt_hp.exceptions import QuiltAuthError, QuiltError
from quilt_hp.models.comfort import ComfortSetting
from quilt_hp.models.controller import Controller
from quilt_hp.models.indoor_unit import IndoorUnit
from quilt_hp.models.outdoor_unit import OutdoorUnit
from quilt_hp.models.qsm import QuiltSmartModule
from quilt_hp.models.sensor import ControllerRemoteSensor, RemoteSensor
from quilt_hp.models.space import Space
from quilt_hp.models.system import Location, SystemSnapshot

from .const import (
    CONF_POLLING_INTERVAL,
    COORDINATOR_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
    ENERGY_UPDATE_INTERVAL_MINUTES,
)
from .token_store import HATokenStore

_LOGGER = logging.getLogger(__name__)

_ISSUE_STREAM_DEGRADED: str = "stream_degraded"

# Back-off bounds for restarting a permanently dead stream.
_STREAM_RESTART_INITIAL_DELAY_S: float = 30.0
_STREAM_RESTART_MAX_DELAY_S: float = 600.0

# Stream states in which the library's reconnect loop has exited for good.
_STREAM_DEAD_STATES: frozenset[str] = frozenset({"stopped", "error"})


class QuiltCoordinator(DataUpdateCoordinator[SystemSnapshot]):
    """Manages the QuiltClient connection and drives entity updates.

    Initial state is fetched via ``get_snapshot()`` on setup, then
    a ``NotifierStream`` pushes real-time diffs directly into the
    coordinator's data. A periodic poll acts as a fallback only.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        email: str,
        system_id: str | None = None,
    ) -> None:
        """Initialize the coordinator."""
        self._poll_minutes: int = entry.options.get(
            CONF_POLLING_INTERVAL, COORDINATOR_UPDATE_INTERVAL_MINUTES
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=self._poll_minutes),
        )
        token_store = HATokenStore(hass)
        self._client: QuiltClient = QuiltClient(email, token_store=token_store)
        self._system_id: str | None = system_id  # None → library picks default
        self._stream: NotifierStream | None = None
        self._stream_death_count: int = 0
        self._stream_connected_once: bool = False
        self._stream_restart_task: asyncio.Task[None] | None = None
        self._was_available: bool = True  # Track connection state for logging
        self._full_refresh_inflight: bool = False
        self._last_full_fetch: datetime | None = None
        self.spaces_by_id: dict[str, Space] = {}
        self.idu_by_id: dict[str, IndoorUnit] = {}
        self.idu_by_space_id: dict[str, IndoorUnit] = {}
        self.first_idu_id_by_space_id: dict[str, str] = {}
        self.odu_by_id: dict[str, OutdoorUnit] = {}
        self.ctrl_by_id: dict[str, Controller] = {}
        self.qsm_by_id: dict[str, QuiltSmartModule] = {}
        self.cs_by_id: dict[str, ComfortSetting] = {}
        self.cs_by_space_id: dict[str, list[ComfortSetting]] = {}
        self.remote_sensor_by_id: dict[str, RemoteSensor] = {}
        self.ctrl_remote_sensor_by_id: dict[str, ControllerRemoteSensor] = {}
        self.location_by_id: dict[str, Location] = {}
        # Energy data — updated at most every ENERGY_UPDATE_INTERVAL_MINUTES
        self.energy_by_space_id: dict[str, float] = {}
        self.energy_last_reset: datetime | None = None
        self._energy_last_attempt: datetime | None = None
        self._energy_fetch_inflight: bool = False

    # ------------------------------------------------------------------
    # Indexed lookups
    # ------------------------------------------------------------------

    def _rebuild_indexes(self, data: SystemSnapshot) -> None:
        """Rebuild the indexed lookup dicts from *data*.

        Called from both the stream-push path (``async_set_updated_data``)
        and the polling path (``_async_update_data``) — HA's base
        ``_async_refresh`` assigns ``self.data`` directly, bypassing
        ``async_set_updated_data``.
        """
        self.spaces_by_id = {s.id: s for s in data.spaces}
        self.idu_by_id = {u.id: u for u in data.indoor_units}
        self.idu_by_space_id = {u.space_id: u for u in data.indoor_units if u.space_id}
        first_idu: dict[str, str] = {}
        for idu in data.indoor_units:
            if idu.space_id and idu.space_id not in first_idu:
                first_idu[idu.space_id] = idu.id
        self.first_idu_id_by_space_id = first_idu
        self.odu_by_id = {u.id: u for u in data.outdoor_units}
        self.ctrl_by_id = {c.id: c for c in data.controllers}
        self.qsm_by_id = {q.id: q for q in data.quilt_smart_modules}
        self.cs_by_id = {cs.id: cs for cs in data.comfort_settings}
        cs_by_space: dict[str, list[ComfortSetting]] = {}
        for cs in data.comfort_settings:
            cs_by_space.setdefault(cs.space_id, []).append(cs)
        self.cs_by_space_id = cs_by_space
        self.remote_sensor_by_id = {rs.id: rs for rs in data.remote_sensors}
        self.ctrl_remote_sensor_by_id = {
            crs.id: crs for crs in data.controller_remote_sensors
        }
        self.location_by_id = {loc.id: loc for loc in data.locations}

    @override
    def async_set_updated_data(self, data: SystemSnapshot) -> None:
        """Update the coordinator data and refresh the indexed lookups."""
        self._rebuild_indexes(data)
        super().async_set_updated_data(data)

    # ------------------------------------------------------------------
    # Public API used by __init__.py and entities
    # ------------------------------------------------------------------

    @property
    def client(self) -> QuiltClient:
        """Expose the underlying QuiltClient for entity write operations."""
        return self._client

    @property
    def stream_death_count(self) -> int:
        """Return the number of stream deaths since the last healthy connect."""
        return self._stream_death_count

    @property
    def is_streaming(self) -> bool:
        """Return True when the gRPC stream is connected.

        Entities use this to skip ``async_request_refresh()`` after writes —
        the stream delivers state changes within milliseconds, making an
        immediate poll redundant.
        """
        return self._stream is not None and self._stream.is_connected

    async def async_set_space(self, space: Space, **kwargs: Any) -> Space:
        """Set space fields with one transparent auth-refresh retry."""
        return await self._write(lambda: self._client.set_space(space, **kwargs))

    async def async_set_indoor_unit(
        self, indoor_unit: IndoorUnit, **kwargs: Any
    ) -> IndoorUnit:
        """Set indoor unit fields with one transparent auth-refresh retry."""
        return await self._write(
            lambda: self._client.set_indoor_unit(indoor_unit, **kwargs)
        )

    async def async_set_schedule_execution(self, *, paused: bool) -> None:
        """Pause or resume all schedules with one transparent auth-refresh retry."""
        await self._write(lambda: self._client.set_schedule_execution(paused=paused))

    async def _write[T](self, operation: Callable[[], Awaitable[T]]) -> T:
        """Run a write with auth retry, translating library errors for HA.

        Entity service actions must raise ``HomeAssistantError`` on failure
        so HA reports them to the user (quality scale ``action-exceptions``).
        """
        try:
            return await self._with_auth_retry(operation)
        except HomeAssistantError:
            # Includes ConfigEntryAuthFailed from the auth retry.
            raise
        except QuiltError as err:
            raise HomeAssistantError(
                f"Quilt command failed: {err}",
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err

    async def async_setup(self) -> None:
        """Open gRPC channel, login, fetch initial snapshot, start stream."""
        _ = await self._client.__aenter__()
        try:
            await self._client.login()

            snapshot = await self._client.get_snapshot(system_id=self._system_id)
            self._last_full_fetch = dt_util.utcnow()
            self.async_set_updated_data(snapshot)

            await self._async_update_energy()
            await self._start_stream(snapshot)
        except BaseException:
            # Close the client if any setup step fails (including a
            # CancelledError from the caller's setup timeout) to avoid
            # leaking the gRPC channel across ConfigEntryNotReady retries.
            with contextlib.suppress(Exception):
                await self._client.__aexit__(None, None, None)
            raise

    @override
    async def async_shutdown(self) -> None:
        """Stop the poll timer, the stream, and close the gRPC channel."""
        await super().async_shutdown()

        if self._stream is not None:
            with contextlib.suppress(Exception):
                await self._stream.stop()
            self._stream = None

        with contextlib.suppress(Exception):
            await self._client.__aexit__(None, None, None)

        async_delete_issue(self.hass, DOMAIN, _ISSUE_STREAM_DEGRADED)

    # ------------------------------------------------------------------
    # Stream management
    # ------------------------------------------------------------------

    async def _start_stream(self, snapshot: SystemSnapshot) -> None:
        topics = snapshot.stream_topics()
        stream = self._client.stream(topics, max_reconnects=-1)

        _ = stream.on_space_update(
            self._make_stream_handler(SystemSnapshot.apply_space)
        )
        _ = stream.on_indoor_unit_update(
            self._make_stream_handler(SystemSnapshot.apply_indoor_unit)
        )
        _ = stream.on_outdoor_unit_update(
            self._make_stream_handler(SystemSnapshot.apply_outdoor_unit)
        )
        _ = stream.on_controller_update(
            self._make_stream_handler(SystemSnapshot.apply_controller)
        )
        _ = stream.on_qsm_update(self._make_stream_handler(SystemSnapshot.apply_qsm))
        _ = stream.on_remote_sensor_update(
            self._make_stream_handler(SystemSnapshot.apply_remote_sensor)
        )
        _ = stream.on_controller_remote_sensor_update(
            self._make_stream_handler(SystemSnapshot.apply_controller_remote_sensor)
        )
        _ = stream.on_error(self._on_stream_error)
        _ = stream.on_connected(self._on_stream_connected)

        await stream.start()
        # Only assign after successful start so async_shutdown doesn't try to
        # stop a stream that never began.
        self._stream = stream

    def _make_stream_handler[M](
        self, apply: Callable[[SystemSnapshot, M], M]
    ) -> Callable[[M], None]:
        """Build a push handler that merges *apply*'s model into the snapshot."""

        def _handler(model: M) -> None:
            if self.data:
                _ = apply(self.data, model)
                self.async_set_updated_data(self.data)
            self._on_stream_push()

        return _handler

    def _on_stream_error(self, err: object) -> None:
        """Handle permanent stream death.

        With ``max_reconnects=-1`` the library reconnects internally on all
        transient failures; ``on_error`` fires only when the stream task has
        exited for good (e.g. a token refresh failed). Surface a repair
        issue and keep retrying a full restart with back-off — the polling
        fallback covers state in the meantime.
        """
        self._stream_death_count += 1
        _LOGGER.warning(
            "Quilt stream died (%s); falling back to polling and retrying", err
        )
        async_create_issue(
            self.hass,
            DOMAIN,
            _ISSUE_STREAM_DEGRADED,
            is_fixable=False,
            severity=IssueSeverity.WARNING,
            translation_key="stream_degraded",
            translation_placeholders={"interval": str(self._poll_minutes)},
        )
        self._schedule_stream_restart()

    def _schedule_stream_restart(self) -> None:
        """Schedule a background task that restarts the dead stream."""
        if (
            self._stream_restart_task is not None
            and not self._stream_restart_task.done()
        ):
            return
        if self.config_entry is None:
            return
        self._stream_restart_task = self.config_entry.async_create_background_task(
            self.hass,
            self._restart_stream_with_backoff(),
            name="quilt_hp-stream-restart",
        )

    async def _restart_stream_with_backoff(self) -> None:
        """Retry restarting the stream until it succeeds or auth fails."""
        delay = _STREAM_RESTART_INITIAL_DELAY_S
        while True:
            await asyncio.sleep(delay)
            try:
                if self._stream is not None:
                    with contextlib.suppress(Exception):
                        await self._stream.stop()
                    self._stream = None
                await self._start_stream(self.data)
            except QuiltAuthError as err:
                _LOGGER.error("Quilt stream restart failed authentication: %s", err)
                if self.config_entry is not None:
                    self.config_entry.async_start_reauth(self.hass)
                return
            except Exception as err:
                _LOGGER.debug(
                    "Quilt stream restart failed (%s); retrying in %.0fs", err, delay
                )
                delay = min(delay * 2, _STREAM_RESTART_MAX_DELAY_S)
            else:
                _LOGGER.info("Quilt stream restarted")
                return

    def _on_stream_connected(self) -> None:
        """Handle the stream (re)connecting.

        Events published while the stream was disconnected are lost, so on
        any reconnect after the initial connection we schedule a full
        refresh to close the gap instead of waiting for the next poll. The
        refresh is un-debounced: a debounced request would be cancelled by
        the first push after reconnect (``async_set_updated_data`` cancels
        the pending debouncer).
        """
        is_reconnect = self._stream_connected_once
        self._stream_connected_once = True
        if self._stream_death_count > 0:
            _LOGGER.info("Quilt stream connection restored")
        self._stream_death_count = 0
        async_delete_issue(self.hass, DOMAIN, _ISSUE_STREAM_DEGRADED)
        if is_reconnect and self.config_entry is not None:
            self.config_entry.async_create_background_task(
                self.hass,
                self._async_full_refresh(),
                name="quilt_hp-reconnect-refresh",
            )

    def _on_stream_push(self) -> None:
        """Run rate-limited side effects on every stream push.

        Pushes bypass the poll path where energy is normally fetched, and
        HA reschedules the poll timer on every ``async_set_updated_data`` —
        so a busy stream starves the poll entirely. Both concerns are
        handled here with cheap synchronous checks before any task is
        spawned: refresh energy when due, and force a full snapshot when
        the last one is older than the poll interval (Locations and comfort
        settings are not streamed).
        """
        if self.config_entry is None:
            return
        now = dt_util.utcnow()
        if not self._energy_fetch_inflight and self._energy_refresh_due(now):
            self.config_entry.async_create_background_task(
                self.hass,
                self._update_energy_and_notify(),
                name="quilt_hp-energy-refresh",
            )
        if not self._full_refresh_inflight and self._snapshot_stale(now):
            self.config_entry.async_create_background_task(
                self.hass,
                self._async_full_refresh(),
                name="quilt_hp-stale-snapshot-refresh",
            )

    def _energy_refresh_due(self, now: datetime) -> bool:
        return (
            self._energy_last_attempt is None
            or now - self._energy_last_attempt
            >= timedelta(minutes=ENERGY_UPDATE_INTERVAL_MINUTES)
        )

    def _snapshot_stale(self, now: datetime) -> bool:
        return (
            self._last_full_fetch is None
            or now - self._last_full_fetch >= timedelta(minutes=self._poll_minutes)
        )

    async def _async_full_refresh(self) -> None:
        """Run an un-debounced full refresh, guarded against overlap."""
        if self._full_refresh_inflight:
            return
        self._full_refresh_inflight = True
        try:
            await self.async_refresh()
        finally:
            self._full_refresh_inflight = False

    async def _update_energy_and_notify(self) -> None:
        """Fetch energy and trigger entity updates if new data was retrieved.

        ``_async_update_energy`` is rate-limited and silently exits early when
        the last fetch is recent.  We only call ``async_set_updated_data`` when
        a fetch actually happened so we don't issue spurious notifications on
        every stream push.
        """
        before = self._energy_last_attempt
        try:
            await self._async_update_energy()
        except ConfigEntryAuthFailed:
            if self.config_entry is not None:
                self.config_entry.async_start_reauth(self.hass)
            return
        if self._energy_last_attempt != before and self.data is not None:  # pyright: ignore[reportUnnecessaryComparison]
            self.async_set_updated_data(self.data)

    # ------------------------------------------------------------------
    # Polling fallback
    # ------------------------------------------------------------------

    @override
    async def _async_update_data(self) -> SystemSnapshot:
        try:
            self._client.invalidate_snapshot()
            snapshot = await self._with_auth_retry(
                lambda: self._client.get_snapshot(system_id=self._system_id)
            )

            # Log once when connection is restored
            if not self._was_available:
                _LOGGER.info("Quilt connection restored")
                self._was_available = True

        except ConfigEntryAuthFailed:
            raise
        except Exception as err:
            # Log once when connection is lost
            if self._was_available:
                _LOGGER.warning("Quilt connection lost: %s", err)
                self._was_available = False
            raise UpdateFailed(f"Error fetching Quilt snapshot: {err}") from err

        # A cleanly ended stream exits without any callback — detect it here
        # (no pushes also means the poll timer is running) and restart it.
        if (
            self._stream is not None
            and self._stream.stream_state in _STREAM_DEAD_STATES
        ):
            self._schedule_stream_restart()

        await self._async_update_energy()
        self._last_full_fetch = dt_util.utcnow()
        # HA assigns self.data directly from the return value, bypassing
        # async_set_updated_data — rebuild the entity lookups here.
        self._rebuild_indexes(snapshot)
        return snapshot

    async def _async_update_energy(self) -> None:
        """Fetch today's energy metrics from the API, rate-limited.

        The rate limit is keyed on the *attempt* time (not success) so a
        failing energy endpoint is retried at the normal cadence instead of
        on every stream push, and an in-flight flag prevents concurrent
        duplicate RPCs from a burst of pushes.
        """
        now = dt_util.utcnow()
        if self._energy_fetch_inflight or not self._energy_refresh_due(now):
            return
        self._energy_fetch_inflight = True
        self._energy_last_attempt = now
        try:
            start = dt_util.start_of_local_day()
            metrics = await self._with_auth_retry(
                lambda: self._client.get_energy(start, now, system_id=self._system_id)
            )
            self.energy_by_space_id = {m.space_id: m.total_kwh for m in metrics}
            self.energy_last_reset = start
        except ConfigEntryAuthFailed:
            raise
        except Exception as err:
            _LOGGER.warning("Failed to fetch Quilt energy data: %s", err)
        finally:
            self._energy_fetch_inflight = False

    async def _with_auth_retry[T](self, operation: Callable[[], Awaitable[T]]) -> T:
        """Retry one operation after re-login when authentication has expired.

        The library's transport refreshes access tokens transparently; a
        ``QuiltAuthError`` surfacing here means the refresh token itself was
        rejected. One explicit re-login is attempted (it may pick up rotated
        tokens from the shared store); if that fails — or the retried
        operation still isn't authenticated — ``ConfigEntryAuthFailed`` is
        raised, triggering HA's reauth flow.
        """
        try:
            return await operation()
        except QuiltAuthError:
            pass

        # Authentication rejected — attempt re-login once
        try:
            await self._client.login()
        except QuiltError as auth_err:
            _LOGGER.error("Quilt re-authentication failed: %s", auth_err)
            raise ConfigEntryAuthFailed(
                "Quilt authentication failed. Please re-authenticate.",
                translation_domain=DOMAIN,
                translation_key="auth_failed",
            ) from auth_err

        try:
            return await operation()
        except QuiltAuthError as err:
            raise ConfigEntryAuthFailed(
                "Quilt authentication failed. Please re-authenticate.",
                translation_domain=DOMAIN,
                translation_key="auth_failed",
            ) from err
