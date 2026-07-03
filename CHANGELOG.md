# Changelog

## [Unreleased]

### Changed
- Upgraded `quilt-hp-python` dependency to `>=0.5.4`
  - Fixes proto3 absence detection: sparse stream diffs no longer zero room state, IDU
    controls, sensor readings, or controller temperatures when merged into the snapshot
  - Fixes the transport `UNAUTHENTICATED` retry that never executed, which broke clients
    permanently about an hour after token expiry
  - Stream reconnect backoff and budget now reset after a healthy connection, so routine
    server-side stream recycling no longer escalates to permanent 60-second delays or
    kills the stream; non-gRPC failures reconnect instead of dying silently
- Stream health is now tracked via the library's new `on_connected` callback instead of
  being inferred from incoming entity events

### Fixed
- On stream reconnect the coordinator now schedules an immediate snapshot refresh to
  recover events published while disconnected, instead of waiting up to the full polling
  interval

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
