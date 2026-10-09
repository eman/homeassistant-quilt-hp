"""DataUpdateCoordinator for the Quilt Heat Pump integration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
import dataclasses
from datetime import datetime, timedelta
import logging
from typing import Any, override

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_call_later
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
from .registry import async_remove_deleted
from .token_store import HATokenStore

_LOGGER = logging.getLogger(__name__)

_ISSUE_STREAM_DEGRADED: str = "stream_degraded"

# Back-off bounds for restarting a permanently dead stream.
_STREAM_RESTART_INITIAL_DELAY_S: float = 30.0
_STREAM_RESTART_MAX_DELAY_S: float = 600.0

# Stream states in which the library's reconnect loop has exited for good.
_STREAM_DEAD_STATES: frozenset[str] = frozenset({"stopped", "error"})

# How long a requested self-test counts as running before the unit reports it.
# The library saw a unit enter its test within 15 s of the request.
SELF_TEST_PENDING_S: float = 60.0

# Controller (Dial) readings that come from its ``state`` sub-message.
_CONTROLLER_READINGS: tuple[str, ...] = (
    "raw_thermistor_c",
    "pcb_temperature_a_c",
    "pcb_temperature_b_c",
    "calibrated_ambient_c",
    "screen_brightness",
    "radar_target_detected",
    "radar_phase_detected",
    "ambient_light_lux",
    "humidity_percent",
    "power_w",
    "main_board_temperature_c",
    "power_board_temperature_c",
    "accelerometer_raw",
)


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
        self._full_refresh_queued: bool = False
        self._last_full_fetch: datetime | None = None
        self._stream_topics: set[str] = set()
        # Stream deletions not yet confirmed by a full fetch, with the value of
        # ``_deletion_seq`` when each arrived.
        self._deletion_seq: int = 0
        self._pending_deletions: dict[tuple[str, str], int] = {}
        self._self_test_requested_at: dict[str, datetime] = {}
        self._cancel_callbacks: set[CALLBACK_TYPE] = set()
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
        """Set space fields with one transparent auth-refresh retry.

        The returned (full) Space is merged into the snapshot and pushed to
        entities immediately, so the UI reflects the change without waiting
        for the next stream push — a controls-only change may otherwise not be
        echoed on the notifier stream, making a write appear to have no effect.
        """
        result = await self._write(lambda: self._client.set_space(space, **kwargs))
        if self.data:
            _ = self.data.apply_space(result)
            self.async_set_updated_data(self.data)
        return result

    async def async_set_indoor_unit(
        self, indoor_unit: IndoorUnit, **kwargs: Any
    ) -> IndoorUnit:
        """Set indoor unit fields with one transparent auth-refresh retry.

        The returned (full) IndoorUnit is merged into the snapshot and pushed
        to entities immediately, so the UI reflects the change without waiting
        for the next stream push — a controls-only change (e.g. LED/fan/louver)
        may otherwise not be echoed on the notifier stream, making a write
        appear to have no effect.
        """
        result = await self._write(
            lambda: self._client.set_indoor_unit(indoor_unit, **kwargs)
        )
        if self.data:
            _ = self.data.apply_indoor_unit(result)
            self.async_set_updated_data(self.data)
        return result

    async def async_set_controller(
        self, controller: Controller, **kwargs: Any
    ) -> Controller:
        """Set Dial (controller) fields with one transparent auth-refresh retry.

        The returned Controller is merged into the snapshot and pushed to
        entities immediately, like ``async_set_indoor_unit``. The server's
        reply carries no hardware fields; ``apply_controller`` keeps them.
        """
        result = await self._write(
            lambda: self._client.set_controller(controller, **kwargs)
        )
        if self.data:
            _ = self.data.apply_controller(result)
            self.async_set_updated_data(self.data)
        return result

    async def async_start_self_test(self, indoor_unit: IndoorUnit) -> None:
        """Start an indoor unit's diagnostic self-test.

        The unit only reports the test some seconds later, so the request is
        remembered (see ``self_test_active``) and, without the stream to push
        the change, a poll is scheduled for when the unit should report it.
        """
        await self._write(lambda: self._client.start_self_test(indoor_unit))
        self._self_test_requested_at[indoor_unit.id] = dt_util.utcnow()
        self.async_update_listeners()
        if not self.is_streaming:
            self._async_call_later(SELF_TEST_PENDING_S, self._async_full_refresh)

    async def async_cancel_self_test(self, indoor_unit: IndoorUnit) -> None:
        """Cancel an indoor unit's running diagnostic self-test."""
        await self._write(lambda: self._client.cancel_self_test(indoor_unit))
        _ = self._self_test_requested_at.pop(indoor_unit.id, None)
        self.async_update_listeners()

    def self_test_active(self, indoor_unit: IndoorUnit) -> bool:
        """Return True while *indoor_unit* runs a test or one was just requested."""
        if indoor_unit.is_under_test:
            return True
        requested_at = self._self_test_requested_at.get(indoor_unit.id)
        return requested_at is not None and dt_util.utcnow() - requested_at < timedelta(
            seconds=SELF_TEST_PENDING_S
        )

    def _async_call_later(
        self, delay: float, action: Callable[[], Awaitable[None]]
    ) -> None:
        """Run *action* after *delay* seconds unless the coordinator shuts down."""
        cancel: CALLBACK_TYPE | None = None

        async def _run(_now: datetime) -> None:
            if cancel is not None:
                self._cancel_callbacks.discard(cancel)
            await action()

        cancel = async_call_later(
            self.hass, delay, HassJob(_run, cancel_on_shutdown=True)
        )
        self._cancel_callbacks.add(cancel)

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

        for cancel in self._cancel_callbacks:
            cancel()
        self._cancel_callbacks.clear()

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
        _ = stream.on_controller_update(self._make_stream_handler(_apply_controller))
        _ = stream.on_qsm_update(self._make_stream_handler(SystemSnapshot.apply_qsm))
        _ = stream.on_remote_sensor_update(
            self._make_stream_handler(SystemSnapshot.apply_remote_sensor)
        )
        _ = stream.on_controller_remote_sensor_update(
            self._make_stream_handler(SystemSnapshot.apply_controller_remote_sensor)
        )
        _ = stream.on_delete(self._on_stream_delete)
        _ = stream.on_error(self._on_stream_error)
        _ = stream.on_connected(self._on_stream_connected)

        await stream.start()
        # Only assign after successful start so async_shutdown doesn't try to
        # stop a stream that never began.
        self._stream = stream
        self._stream_topics = set(topics)

    def _subscribe_new_topics(self, snapshot: SystemSnapshot) -> None:
        """Subscribe the stream to objects added since it started.

        The stream only carries the topics it subscribed to, so a Dial or
        indoor unit added later would otherwise be updated only by polls.
        """
        stream = self._stream
        if stream is None or self.config_entry is None:
            return
        new_topics = [
            t for t in snapshot.stream_topics() if t not in self._stream_topics
        ]
        if not new_topics:
            return
        self._stream_topics.update(new_topics)

        async def _subscribe() -> None:
            try:
                await stream.subscribe(new_topics)
            except Exception as err:
                _LOGGER.debug("Quilt stream subscribe failed: %s", err)
                self._stream_topics.difference_update(new_topics)

        self.config_entry.async_create_background_task(
            self.hass, _subscribe(), name="quilt_hp-stream-subscribe"
        )

    def _make_stream_handler[M](
        self, apply: Callable[[SystemSnapshot, M], M]
    ) -> Callable[[M], None]:
        """Build a push handler that merges *apply*'s model into the snapshot."""

        def _handler(model: M) -> None:
            if self.data:
                _ = apply(self.data, model)
                self._subscribe_new_topics(self.data)
                self.async_set_updated_data(self.data)
            self._on_stream_push()

        return _handler

    def _on_stream_delete(self, kind: str, entity_id: str) -> None:
        """Drop an object the server deleted and confirm it with a full fetch.

        Deletions never reach the ``on_*_update`` callbacks. Its entities go
        unavailable right away. The confirming fetch then removes the device
        from the registry if the object is really gone, or restores it if it
        isn't: ``remove`` tombstones the object in the current snapshot, which
        would also swallow its re-creation when it moves (e.g. a Dial moved to
        another room may be deleted from one room and created in the other).
        """
        if not (self.data and self.data.remove(kind, entity_id)):
            return
        _LOGGER.debug("Quilt %s %s was deleted", kind, entity_id)
        self._deletion_seq += 1
        self._pending_deletions[(kind, entity_id)] = self._deletion_seq
        self.async_set_updated_data(self.data)
        if self.config_entry is not None:
            self.config_entry.async_create_background_task(
                self.hass,
                self._async_full_refresh(),
                name="quilt_hp-deletion-refresh",
            )

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
        """Run an un-debounced full refresh, guarded against overlap.

        A refresh requested while one is running runs once more after it, since
        the running fetch may predate whatever prompted the request.
        """
        if self._full_refresh_inflight:
            self._full_refresh_queued = True
            return
        self._full_refresh_inflight = True
        try:
            while True:
                self._full_refresh_queued = False
                await self.async_refresh()
                if not self._full_refresh_queued:
                    break
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
        deletion_seq = self._deletion_seq
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

        self._apply_pending_deletions(snapshot, deletion_seq)
        self._subscribe_new_topics(snapshot)
        await self._async_update_energy()
        self._last_full_fetch = dt_util.utcnow()
        # HA assigns self.data directly from the return value, bypassing
        # async_set_updated_data — rebuild the entity lookups here.
        self._rebuild_indexes(snapshot)
        return snapshot

    def _apply_pending_deletions(
        self, snapshot: SystemSnapshot, deletion_seq: int
    ) -> None:
        """Reconcile stream deletions with a freshly fetched *snapshot*.

        *deletion_seq* is ``_deletion_seq`` from before the fetch. A deletion
        that arrived during the fetch is re-applied, since the fetch may
        predate it; its own confirming fetch follows. One that arrived before
        is settled by the fetch: if the object is gone its device is removed
        from the registry; if it's still there it was moved, not deleted.
        """
        for key, seq in list(self._pending_deletions.items()):
            kind, object_id = key
            if seq > deletion_seq:
                _ = snapshot.remove(kind, object_id)
                continue
            del self._pending_deletions[key]
            if not _snapshot_has(snapshot, kind, object_id) and self.config_entry:
                async_remove_deleted(
                    self.hass, self.config_entry.entry_id, kind, object_id
                )

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


def _snapshot_has(snapshot: SystemSnapshot, kind: str, object_id: str) -> bool:
    """Return True if *snapshot* holds the stream object *kind*/*object_id*."""
    items: list[Any] = getattr(snapshot, _SNAPSHOT_ATTR_BY_KIND.get(kind, ""), [])
    return any(item.id == object_id for item in items)


# Snapshot list holding each stream object kind (``NotifierStream.on_delete``).
_SNAPSHOT_ATTR_BY_KIND: dict[str, str] = {
    "space": "spaces",
    "indoor_unit": "indoor_units",
    "outdoor_unit": "outdoor_units",
    "controller": "controllers",
    "qsm": "quilt_smart_modules",
    "remote_sensor": "remote_sensors",
    "controller_remote_sensor": "controller_remote_sensors",
    "software_update_info": "software_update_infos",
}


def _apply_controller(snapshot: SystemSnapshot, ctrl: Controller) -> Controller:
    """Merge a stream-updated Dial, treating an empty ``state`` as offline.

    The server sends an offline Dial with an empty ``state``: no timestamp,
    and zeros for every reading. ``apply_controller`` keeps the previous
    timestamp when the new one is missing, so the Dial would stay online for
    up to 5 minutes reporting 0 °C. Clear the readings and the timestamp
    instead, so it goes offline at once, as it would after a full fetch.
    """
    # The library parses ``screen_brightness`` as None only when ``state``
    # was absent from the update, as it is in a sparse diff.
    if ctrl.state_updated_at is not None or ctrl.screen_brightness is None:
        return snapshot.apply_controller(ctrl)
    cleared: dict[str, Any] = dict.fromkeys(_CONTROLLER_READINGS)
    offline = dataclasses.replace(
        snapshot.apply_controller(ctrl), state_updated_at=None, **cleared
    )
    # Not found when the Dial was deleted and apply_controller ignored it.
    for i, existing in enumerate(snapshot.controllers):
        if existing.id == offline.id:
            snapshot.controllers[i] = offline
    return offline
