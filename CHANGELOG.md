# Changelog

## [Unreleased]

### Changed
- Upgraded `quilt-hp-python` dependency to `>=0.5.5`
  - Routine hourly token refresh on unary RPCs is now logged at `INFO` instead of
    `WARNING` ([quilt-hp-python#13](https://github.com/eman/quilt-hp-python/issues/13))
  - Includes all 0.5.4 fixes: proto3 absence detection (sparse stream diffs no longer
    zero room state, IDU controls, sensor readings, or controller temperatures when
    merged into the snapshot), the transport `UNAUTHENTICATED` retry that never
    executed (clients broke permanently about an hour after token expiry), and stream
    reconnect backoff/budget reset after a healthy connection (routine server-side
    stream recycling no longer escalates to permanent 60-second delays or kills the
    stream; non-gRPC failures reconnect instead of dying silently)
- Stream health is now tracked via the library's new `on_connected` callback instead of
  being inferred from incoming entity events
- Sensor, binary sensor, and louver angle names now use translation keys
  with HA sentence-case naming (e.g. "Wi-Fi signal"); the comms-health
  sensor is a proper `ENUM` sensor with translated states
- Louver angle select options are now lowercase translated keys (`horizontal`,
  `slightly_down`, `down`, `mostly_down`, `straight_down`) instead of raw enum names
- Entity service actions now raise `HomeAssistantError` when a Quilt API call fails
  (previously a silent warning)
- The `stream_degraded` repair issue is created as soon as the stream dies for good,
  shows the actual configured polling interval, and is removed on unload
- Schedule switch writes always trigger an immediate poll (location state is not
  carried on the real-time stream)
- Energy "today" window and `last_reset` now use local midnight instead of UTC midnight
- `manifest.json` now declares `integration_type: hub` and `loggers`
- `quality_scale.yaml` rewritten in the standard machine-readable rules format
- Rebuilt developer typing setup: mypy resolves the integration as
  `custom_components.quilt_hp` (no longer shadowing the `quilt_hp` library, which had
  silently degraded all library types to `Any`), and the `attr-defined`/`unused-ignore`
  error codes are re-enabled

### Added
- Per-space **Active comfort setting** diagnostic sensor (`ENUM`: active/sleep/away/
  standby/custom) reporting which comfort profile Quilt's scheduler is currently applying
  to a room; the profile's configured name is exposed as the `comfort_setting_name`
  attribute. This replaces the removed climate preset control with a read-only,
  non-misleading view of the same state.
- Indoor unit fan speed is now a **`select`** entity (Auto / Quiet / Low / Medium / High /
  Blast). Quilt's fan is always in one of these modes — there is no "off" — so a select
  models it correctly, unlike a fan entity's on/off toggle.
- Exception translations: entity action failures and re-authentication errors now use
  translatable messages (`exceptions` section in `strings.json`)
- README: use cases and automation examples (presence-based setback, schedule
  pausing, energy budgets, LED notifications) validated against a live system
- Dynamic device support: indoor units, spaces, controllers, remote sensors, and
  locations added to the Quilt account after setup now appear without a reload, and
  devices removed from the account are cleaned from the HA registry at setup
- Climate entities support `climate.turn_on` / `climate.turn_off`
  (`ClimateEntityFeature.TURN_ON | TURN_OFF`)
- Config flow: `data_description` help text for all input fields; reconfigure now
  validates that the entry's home exists on the new account and keeps the entry's
  unique ID consistent when the email changes
- Cached authentication tokens are deleted from HA storage when the last config entry
  for an account is removed
- Removal instructions in the README

### Removed
- The `fan` platform / fan entity. The indoor unit fan cannot be turned off, so an HA fan
  entity (which forces on/off semantics and previously faked "off" = Auto) was misleading.
  Fan speed is now a `select` entity (see Added).
- The **Fan speed (RPM)** and **Fan speed setpoint (RPM)** diagnostic sensors. Raw fan RPM
  is a low-level reading; the meaningful fan speed (the `FanSpeed` enum) is surfaced by the
  new fan speed select.
- Climate preset support (`ClimateEntityFeature.PRESET_MODE`). Quilt's comfort settings
  (Active/Sleep/Away/Standby/Custom) are internal schedule states applied automatically
  by Quilt's scheduler, not user-selectable presets, so exposing them as HA presets was
  misleading. Setpoint display still falls back to the active comfort setting's values
  when the API returns placeholder setpoints.

### Fixed
- Control writes (LED on/off, fan speed, louver, climate setpoints/mode) now update the
  entity state immediately from the write's return value instead of waiting for the next
  stream push. A controls-only change is not always echoed on the notifier stream, so
  toggling the LED light (and other controls) could appear to do nothing while streaming.
- Turning the indoor unit LED on when its stored color was black (color code 0) now
  defaults to white, so the light actually illuminates instead of staying dark.
- Fallback polling never refreshed the coordinator's entity lookup indexes, so all
  entities froze at their last streamed state whenever the stream was down and only
  recovered on the next push
- Authentication-failure detection was dead code: the coordinator matched an error
  string ("jwt is expired") that quilt-hp-python never produces, so an expired refresh
  token caused endless `UpdateFailed` errors instead of triggering HA's re-auth flow;
  `QuiltAuthError` is now mapped to `ConfigEntryAuthFailed` in the polling, write, and
  setup paths
- `async_setup_entry` converted auth failures into `ConfigEntryNotReady`, retrying setup
  forever instead of starting re-authentication
- Re-authentication ended in an `already_configured` abort without updating or reloading
  the entry (and could create a duplicate entry when listing systems failed); it now
  ends with `reauth_successful` and reloads the entry
- Coordinator shutdown skipped `super().async_shutdown()`, leaving the poll timer and
  debouncer running against a closed gRPC channel after unload
- A permanently dead stream (e.g. after a failed token refresh) was invisible:
  `is_streaming` stayed `True` forever, post-write refreshes were skipped, and the
  repair issue could never trigger; the coordinator now restarts a dead stream with
  backoff, detects cleanly-ended streams from the poll path, and reports `is_streaming`
  truthfully
- The reconnect gap-fill refresh used the debounced request path, so the first push
  after a reconnect cancelled it and state changed while disconnected could be lost
  until the next poll; the refresh is now un-debounced
- Frequent stream pushes rescheduled HA's poll timer indefinitely, so locations and
  comfort settings (which are not streamed) went permanently stale in busy homes; a
  stale-snapshot check now forces a full refresh at the polling cadence
- A setup timeout (`asyncio.timeout` cancellation) bypassed the client cleanup in
  `async_setup`, leaking a gRPC channel on every `ConfigEntryNotReady` retry
- Config flow: adding a duplicate home leaked the paused login task and its gRPC
  channel; abandoning the OTP dialog leaked them permanently (`async_remove` now cleans
  up); an auth error at the email step was reported as "cannot connect" instead of an
  authentication error
- `light.turn_on` with no arguments was a no-op when the device reported the LED off
  with a retained non-zero brightness; the restore guard now keys on the LED state
- Duplicate `"entity"` JSON key in `strings.json`/`translations/en.json` silently
  discarded the louver-mode state translations
- Energy fetches could run concurrently from a burst of stream pushes and were retried
  on every push while the energy endpoint was failing; fetches are now single-flight
  and rate-limited on attempt time
- NaN values from the API are now normalized to "unknown" for all numeric sensors
  (humidity, power, COP, RPM, signal, presence level, light brightness), not just
  temperatures, preventing `ValueError` state writes
- Entities for devices removed from the Quilt account now become unavailable instead of
  raising `KeyError` on every state update
- `hass.async_create_task`-based reauth from stream-triggered energy fetches is guarded
  when the config entry is missing

## [0.5.3] - 2026-07-02

### Fixed
- Stream-triggered energy fetches with an expired JWT could raise `AttributeError` on
  `config_entry.async_start_reauth` when `config_entry` was `None`, and could re-login
  more times than necessary on a single retry; the coordinator now guards the reauth call
  and only re-authenticates once per failed energy fetch (thanks
  [@c00w](https://github.com/c00w),
  [#9](https://github.com/eman/homeassistant-quilt-hp/pull/9))

## [0.5.2] - 2026-06-30

### Fixed
- Upgraded `quilt-hp-python` dependency to `>=0.5.3`
  - Fixes a hang in `_make_cognito_client()` where botocore's EC2 instance metadata
    credential discovery (IMDS at 169.254.169.254) blocked the calling thread
    indefinitely on non-EC2 hosts (e.g. Home Assistant Yellow), exceeding the 20-second
    `async_setup` budget and causing `setup_retry` on every HA restart when the
    Cognito token was expired

## [0.5.1] - 2026-06-30

### Changed
- Upgraded `quilt-hp-python` dependency to `>=0.5.1`
  - `QuiltClient.close()` now clears the cached token, preventing stale token access after close
  - `invalidate_snapshot()` log level lowered from `WARNING` to `DEBUG`, reducing log noise
  - `invoke_refresh_callback` deduplicated into a single shared implementation; eliminates repeated `inspect.signature()` calls on every token-refresh event
  - `FanSpeed.to_wire()` / `LouverAngle.to_wire()` no longer re-allocate mapping dicts on every call

### Fixed
- `diagnostics.py` accessed `hass.data[DOMAIN][entry.entry_id]` (old pattern) instead of
  `entry.runtime_data`; every call to the HA diagnostics page raised `KeyError`
- Config flow reconfigure + OTP: when the user changed email and OTP was required, the
  success path called `_create_entry` and created a duplicate entry instead of updating the
  existing one; added `_reconfigure_entry` tracking so the OTP success path routes to
  `async_update_reload_and_abort` in reconfigure context
- Config flow `async_step_otp`: `FlowError` (e.g. `AbortFlow`) was caught by the generic
  `except Exception` handler and converted to an "unknown" error, silently preventing reauth
  from completing when OTP was required; `FlowError` is now re-raised
- Energy refresh tasks are now created via `config_entry.async_create_background_task` so
  they are cancelled on entry unload rather than outliving it
- Stream-triggered energy fetch did not call `async_set_updated_data` after updating
  `energy_by_space_id`, so energy sensor entities would not re-render until the next
  unrelated stream push; a new `_update_energy_and_notify()` wrapper calls
  `async_set_updated_data` only when a fetch actually occurred (not on rate-limited early return)
- Schedule switch called `coordinator.async_request_refresh()` directly after writes instead
  of `_async_refresh_if_not_streaming()`, causing unnecessary polls while the gRPC stream
  is active
- Removed redundant targeted dict mutations in stream handlers that were immediately
  overwritten by `async_set_updated_data`
- Energy sensors stopped updating while the gRPC stream was active because
  `async_set_updated_data` cancels pending coordinator polls; the coordinator now
  explicitly checks whether an energy refresh is due on each stream push and fetches
  if so, ensuring reliable 30-minute energy updates regardless of stream activity
  (thanks [@c00w](https://github.com/c00w), [#3](https://github.com/eman/homeassistant-quilt-hp/pull/3))

## [0.5.0] - 2026-06-05

### Added
- Support for **Dry Mode** (dehumidification): New `HVACMode.DRY` with automatic humidity control
  - Dry mode states: `HVACState.DRY`, `DRY_DEFERRED`, `DRY_PREPARING`
  - Dry mode maps to `HVACAction.DRYING` in Home Assistant
  - Temperature setpoint display disabled in Dry mode (server-side controlled)
- **Local Comms Health sensors** for QSM (QuiltSmartModule) and Controller (Dial):
  - Diagnostic sensors showing local Wi-Fi communication status
  - Status values: `UNSPECIFIED`, `HEALTHY`, `DEGRADED`, `OFFLINE`, `STARTING_UP`
  - Useful for troubleshooting local control connectivity

### Changed
- Upgraded `quilt-hp-python` dependency to `>=0.5.0`
  - Brings in new DRY mode support and local comms health monitoring
  - `LocalCommsStatus` proto message structure for QSM and Controller
  - `LocalCommsHealthStatus` enum for health state

## [0.4.0] - 2026-05-16

## [0.3.0] - 2026-05-16

### Fixed
- `CancelledError` (a `BaseException` in Python 3.11+) now correctly suppressed when cancelling the in-flight login task and when stopping the gRPC stream; previously it propagated and caused unhandled exceptions
- `coordinator.async_setup()` now closes the API client if stream setup fails, preventing a resource leak
- Energy window start date now derived from the UTC clock (`now.date()`) instead of `date.today()` (local time), preventing off-by-one errors on servers not running in UTC
- Config flow home selection now disambiguates duplicate home names with numeric suffixes (e.g. "Home (2)") so users can distinguish between homes with identical names
- `HATokenStore.load()` now handles malformed persisted data (wrong type, missing keys) gracefully instead of crashing with `TypeError` or `AttributeError`
- `normalize_temperature()` now handles non-float numeric inputs (e.g. `int`) without raising `TypeError`

### Added
- Test coverage raised from 82% to 97% (252 tests)
- **Entity category assignments (Gold tier)**: Diagnostic sensors now properly categorized
  - Battery, signal strength, online status → DIAGNOSTIC
  - Performance metrics (COP, RPM, pressures, etc.) → DIAGNOSTIC
  - Radar/ALS sensors → DIAGNOSTIC
  - WiFi/PCB diagnostics → DIAGNOSTIC
  - Primary sensors (temperature, humidity, power) remain uncategorized for dashboard prominence
- **Silver tier compliance**: Integration now meets Home Assistant Silver tier requirements
  - Smart connection state logging (log once on loss, once on restore)
  - PARALLEL_UPDATES constants to prevent overwhelming devices
  - Comprehensive documentation with troubleshooting guide
  - Enhanced configuration and installation instructions
- **Reconfigure step** in config flow to allow changing email or re-authenticating without removing integration
- **Entity translations**: All entity names now use translation_key for better internationalization support
- **Error handling improvements**: Added ConfigEntryAuthFailed exception for automatic reauth flow
- **Quality scale documentation**: Added quality_scale.yaml documenting Bronze tier compliance
- **Comprehensive error tests**: Added test_error_handling.py with tests for auth failures, stream errors, and network issues
- More specific error messages in config flow (network_error, invalid_email, otp_expired, api_error)
- README banner image (`images/banner.svg`) and top-of-page banner block
- README link to `quilt-hp-python` docs for protocol, streaming, and feature details

### Changed
- **Device naming improved** to follow Home Assistant best practices. Indoor unit devices now use their configured name (e.g., "Living Room IDU") instead of just the room name, eliminating confusion between device names and area names. Outdoor units now include serial numbers when available for better identification.
- Removed duplicate `_attr_name` settings in favor of `_attr_translation_key` for consistent i18n
- Stream error logging changed from WARNING on every error to single WARNING on loss + single INFO on restore
- Polling fallback now logs connection state transitions instead of every failure

## [0.2.0] - 2026-05-13

### Added
- Energy consumption sensors (power, accumulated energy) for indoor and outdoor units
- Schedule switch entity to enable/disable Quilt scheduling per space
- Comprehensive sensor coverage: space, IDU, ODU, QSM (radar/ALS), and Controller entities
- Controller (Quilt Dial) device with temperature sensor
- Multi-home selection step in config flow
- Brand assets (`icon.png`, `logo.png`, `icon.svg`, `logo.svg`) for HA brands API
- Docker Compose setup for local HA development and testing
- HACS validation GitHub Actions workflow (`hacs/action` + `hassfest`)

### Changed
- Integration display name renamed from "Quilt Heat Pump" to "Quilt"
- Spaces mapped to HA Areas (`suggested_area`) instead of devices; IDU is the primary device per room
- Outdoor unit linked to its indoor unit via `via_device` for correct HA device hierarchy
- ODU sensors created per IDU connection to support multi-IDU scenarios
- Upgraded minimum requirement to `quilt-hp-python>=0.3.0`
- Minimum Home Assistant version set to 2026.3.0

### Fixed
- OTP login flow: keep login task alive across config flow steps to prevent OTP rejection
- Louver angle availability check uses `louver_mode` instead of `louver_fixed_position`
- Louver angle select always returns a valid option
- Outdoor unit linking uses `space_id` relationship
- IDU device model uses `hardware_id` instead of `settings.name`
- `NotifierStream` uses `on_error` callback (replaces non-existent `on_disconnected`)
- All strict mypy and basedpyright type errors resolved

## [0.1.0] - 2025-01-01

### Added
- Initial implementation of the Quilt Heat Pump Home Assistant integration
- OTP-based config flow (email → one-time passcode)
- Climate entity for each Space (HVAC mode, setpoints, current temperature)
- Fan entity for each IndoorUnit (fan speed, oscillation)
- Light entity for each IndoorUnit LED indicator
- Select entity for louver position control
- Binary sensor entities for IndoorUnit state
- Real-time updates via `NotifierStream` gRPC bidirectional stream
- Polling fallback every 5 minutes via `DataUpdateCoordinator`
- JWT token persistence via `HATokenStore` (HA `Storage` API)
- Automatic token refresh with transparent re-login on expiry

[Unreleased]: https://github.com/eman/homeassistant-quilt-hp/compare/v0.5.3...HEAD
[0.5.3]: https://github.com/eman/homeassistant-quilt-hp/compare/v0.5.2...v0.5.3
[0.5.2]: https://github.com/eman/homeassistant-quilt-hp/compare/v0.5.1...v0.5.2
[0.5.1]: https://github.com/eman/homeassistant-quilt-hp/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/eman/homeassistant-quilt-hp/compare/v0.4.0...v0.5.0
[0.2.0]: https://github.com/eman/homeassistant-quilt-hp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/eman/homeassistant-quilt-hp/releases/tag/v0.1.0
