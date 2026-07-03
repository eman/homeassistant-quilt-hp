# Copilot Instructions

## Git Commits

Do not add `Co-authored-by` trailers or any other Copilot attribution to commit messages.

## Versioning and Changelog

This project follows [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html):
- **PATCH** (`0.2.x`): backwards-compatible bug fixes
- **MINOR** (`0.x.0`): backwards-compatible new features
- **MAJOR** (`x.0.0`): incompatible API or breaking changes

All notable changes must be documented in [`CHANGELOG.md`](../CHANGELOG.md) following the
[Keep a Changelog 1.0.0](https://keepachangelog.com/en/1.0.0/) format:
- Add new entries under `## [Unreleased]` as you work
- On release: rename `[Unreleased]` to `[x.y.z] - YYYY-MM-DD`, add a new empty `[Unreleased]` section, and update the comparison links at the bottom
- Use the standard change categories: `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`

**Release process:**
1. Update `manifest.json` `version` field
2. Update `CHANGELOG.md` (move Unreleased → versioned section, add date, update links)
3. Commit: `git commit -m "chore: release vX.Y.Z"`
4. Tag: `git tag vX.Y.Z && git push origin vX.Y.Z`
5. The `release.yml` GitHub Action automatically creates the GitHub release from the tag and CHANGELOG entry

## Local Home Assistant Container

A `docker-compose.yml` is included for running a real HA instance locally for diagnosis and
manual testing. The integration source is bind-mounted read-only into the container, so any
edits to `custom_components/quilt_hp/` are immediately visible — restart HA to reload them.

```bash
docker compose up -d          # start
docker compose restart        # pick up integration changes
docker compose down           # stop
```

HA is available at **http://localhost:8124**. Logs are written to `config/home-assistant.log`.
To enable verbose logging for this integration, add to `config/configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.quilt_hp: debug
```

## Commands

```bash
# Run all tests
pytest

# Run a single test file or test
pytest tests/test_climate.py
pytest tests/test_climate.py::test_hvac_mode_heat

# Lint + format check
ruff check .
ruff format --check .

# Auto-fix lint issues
ruff check --fix .
ruff format .

# Type checking (both are required)
mypy custom_components/quilt_hp
basedpyright custom_components/quilt_hp

# Install dev dependencies
pip install -r requirements-dev.txt
```

Pre-commit runs ruff, mypy, and basedpyright automatically on `custom_components/` files.

## Upstream Dependency

The core API client is [`quilt-hp-python`](https://github.com/eman/quilt-hp-python)
(PyPI: `quilt-hp-python`). It provides `QuiltClient`, all model dataclasses
(`Space`, `IndoorUnit`, `OutdoorUnit`, `QuiltSmartModule`, `Controller`, `SystemSnapshot`),
enums (`quilt_hp.models.enums`), and the `NotifierStream` gRPC streaming interface.
When investigating API behaviour, missing attributes, or model changes, check that repo first.

## Architecture

This is a Home Assistant custom integration for Quilt mini-split HVAC systems. The integration
communicates with the Quilt cloud API via `quilt-hp-python`, a fully-async gRPC client.

**Data flow:**

```
QuiltClient (quilt-hp-python)
  └── QuiltCoordinator (coordinator.py)       ← single source of truth
        ├── initial fetch: get_snapshot()
        ├── real-time: NotifierStream (gRPC bidirectional stream)
        │     table-driven handlers (_make_stream_handler + SystemSnapshot.apply_*)
        │     → async_set_updated_data(); on_connected clears the repair issue and
        │     gap-fills after reconnects; on_error (permanent stream death) raises a
        │     repair issue and restarts the stream with backoff
        └── polling fallback: _async_update_data() every 5 minutes (also forced from
            the push path when the last full snapshot is older than the poll interval,
            since pushes reschedule HA's poll timer)

Index rebuild (_rebuild_indexes) runs on BOTH paths — stream pushes
(async_set_updated_data) and polls (_async_update_data) — and refreshes
spaces_by_id, idu_by_id, idu_by_space_id, first_idu_id_by_space_id, odu_by_id,
ctrl_by_id, qsm_by_id, cs_by_id, location_by_id, …; every update notifies all
subscribed CoordinatorEntity instances → triggers property re-evaluation
```

**Physical model hierarchy (from quilt-hp-python):**

- `SystemSnapshot` — top-level snapshot returned by `get_snapshot()`
- `Space` — a room; holds HVAC mode/setpoints/state (controls + state + settings)
- `IndoorUnit` (IDU) — the wall unit; fan/louver/LED controls; linked to a Space via `space_id`
- `OutdoorUnit` (ODU) — compressor unit; linked to IDUs
- `QuiltSmartModule` (QSM) — radar/ALS sensor module embedded in the IDU
- `Controller` — the Quilt Dial; a physically separate wall controller

**HA platform structure:**

| Platform | Entity | Key model |
|---|---|---|
| `climate` | `QuiltClimateEntity` | `Space` |
| `sensor` | `QuiltSpaceSensor`, `QuiltIDUSensor`, `QuiltODUSensor`, `QuiltQSMSensor`, `QuiltControllerSensor` | various |
| `fan` | `QuiltFanEntity` | `IndoorUnit` |
| `light` | `QuiltLightEntity` | `IndoorUnit` (LED) |
| `select` | `QuiltSelectEntity` | `IndoorUnit` (louver) |
| `binary_sensor` | — | `IndoorUnit` |

All entity classes inherit from `QuiltEntity(CoordinatorEntity[QuiltCoordinator])` defined in
`entity.py`. IDU-backed entities (fan, light, select, IDU/QSM sensors, IDU binary sensors)
extend `QuiltIDUEntity`, controller-backed ones `QuiltControllerEntity` — these provide the
model lookup, `device_info`, and `available` handling. Platform `async_setup_entry` functions
register through `entity.async_setup_dynamic_entities()`, which re-runs entity discovery on
every coordinator update so devices added to the account after setup appear automatically;
stale devices are removed from the registry in `__init__.py` at setup.

**Device grouping in HA UI:**

- IDU + its QSM → one HA device named after the device's configured name (e.g., "Living Room IDU") or with fallback pattern "{space} Indoor Unit"
- ODU → separate device with `via_device` pointing to the IDU in the same space
- Controller (Dial) → separate device with `via_device` pointing to the IDU in the same space
- Spaces are NOT HA devices; they surface as areas via `suggested_area`

**Write operations:**

Entities call `coordinator.async_set_space()` / `async_set_indoor_unit()` /
`async_set_schedule_execution()`. These wrap the client call with one transparent
re-login retry on `QuiltAuthError` (raising `ConfigEntryAuthFailed` if re-login fails)
and translate any other `QuiltError` into `HomeAssistantError` so entity actions
surface failures to the user. Entities then call `_async_refresh_if_not_streaming()`
(schedule switch always refreshes — Location state is not streamed).

**Auth:**

OTP-based config flow (email → one-time passcode). Tokens are persisted via `HATokenStore`
(wraps HA's `Storage` API) and deleted when the last entry for an account is removed. Access
token refresh happens automatically in the library transport; a `QuiltAuthError` surfacing in
the integration means the refresh token was rejected and is mapped to `ConfigEntryAuthFailed`
(HA re-auth flow) in the setup, polling, and write paths.

## Key Conventions

**Sensor description pattern:** All sensor platforms use frozen `@dataclass` subclasses of
`SensorEntityDescription` (e.g. `IDUSensorDescription`, `ODUSensorDescription`) with a
`value_fn: Callable[[Model], Any]` field. Sensor entity classes read `entity_description.value_fn`
in `native_value`. Adding a new sensor = append one entry to the appropriate `*_SENSOR_DESCRIPTIONS`
tuple (with a `translation_key` and a matching `entity.sensor.<key>.name` entry in `strings.json`
and `translations/en.json`); no new class required. Entity names are translated, sentence-case.

**`@override` everywhere:** All HA interface methods/properties that override a base class use
the `@override` decorator from `typing`. Apply it consistently.

**`from __future__ import annotations`:** Every module includes this for deferred evaluation of
annotations; required for Python 3.13 compatibility with the HA typing idioms used here.

**Entity `_attr_*` class variables:** Static entity attributes are set as class-level `_attr_*`
variables (HA convention) rather than in `__init__`. Instance-specific ones (unique_id) are set
in `__init__` as `self._attr_unique_id`.

**Unique IDs:** Follow the pattern `quilt_{type}_{id}_{key}`, e.g.
`quilt_idu_<idu_id>_ambient_temperature`, `quilt_space_<space_id>_climate`.

**Coordinator indexed dicts:** Always look up live model objects via the coordinator's indexed
dicts (`spaces_by_id`, `idu_by_id`, etc.) in entity properties — never cache model references
directly on the entity, as they are replaced on every stream update.

**NaN handling:** Always pass raw float values through `normalize_float()` (from `utils.py`)
to convert `NaN` → `None` before returning from HA entity properties — the Quilt API uses
`NaN` as a "no reading" sentinel and HA rejects `NaN` state writes.

**Mode mapping:** HVAC mode translation between Quilt enums and HA enums lives in module-level
dicts `_Q_TO_HA` / `_HA_TO_Q` in `climate.py`.

**Tests:** Tests use `pytest-homeassistant-custom-component` and `pytest-asyncio` (auto mode).
Fixtures are plain functions (`make_space`, `make_idu`, `make_odu`, `make_snapshot`,
`make_mock_coordinator`) in `tests/conftest.py`. Tests instantiate entity classes directly
against a mock coordinator — no HA config entry setup is needed for unit tests.

**Type checking:** Both `mypy` (strict) and `basedpyright` (all/ultra-strict) are enforced.
mypy runs with `explicit_package_bases` so the integration is module
`custom_components.quilt_hp` — never let the integration package shadow the `quilt_hp`
library. The `quilt_hp` library is fully typed (py.typed); do not add blanket
`type: ignore` comments for it.
