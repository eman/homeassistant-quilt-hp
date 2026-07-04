<p align="center">
  <img src="images/banner.svg" alt="Quilt Home Assistant Integration" width="100%">
</p>

[![Validate](https://github.com/eman/homeassistant-quilt-hp/actions/workflows/validate.yml/badge.svg)](https://github.com/eman/homeassistant-quilt-hp/actions/workflows/validate.yml)
[![GitHub Release](https://img.shields.io/github/v/release/eman/homeassistant-quilt-hp)](https://github.com/eman/homeassistant-quilt-hp/releases)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

A Home Assistant custom component for [Quilt](https://www.quilt.com/) mini-split HVAC systems.

Built on top of [`quilt-hp-python`](https://github.com/eman/quilt-hp-python) — a fully-async
gRPC client that communicates directly with the Quilt cloud API.

For protocol details, streaming behavior, and the full client feature set, see the
[`quilt-hp-python` documentation](https://eman.github.io/quilt-hp-python/).

## Features

- **Climate entities** — control HVAC mode and setpoints for each Quilt space (room)
- **Sensor entities** — ambient temperature, humidity, fan speed, inlet/outlet temps,
  presence level, COP, HVAC power, compressor data, and per-space energy
- **Fan entities** — set indoor unit fan speed (Auto / Quiet / Low / Medium / High / Blast)
- **Light entities** — toggle and dim indoor unit LED, set RGBW color and animation effect
- **Select entities** — louver mode (Closed / Sweep / Fixed / Auto) and fixed angle
- **Binary sensor entities** — motion, presence, occupancy, and connectivity status per IDU
- **Switch entities** — pause or resume all Quilt schedules for a location
- **Real-time updates** — powered by Quilt's bidirectional gRPC stream with auto-reconnect
- **Polling fallback** — configurable interval fetch if the stream is unavailable

## Prerequisites

- A [Quilt](https://www.quilt.com/) account with at least one configured system
- Home Assistant 2026.3 or newer
- Python 3.14.2 or newer (managed automatically by HA)

## Installation

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=eman&repository=homeassistant-quilt-hp)

### HACS (recommended)

1. Add this repository as a custom repository in HACS
   (category: **Integration**, URL: `https://github.com/eman/homeassistant-quilt-hp`).
2. Install **Quilt Heat Pump**.
3. Restart Home Assistant.

### Manual

Copy `custom_components/quilt_hp/` into your HA `config/custom_components/` directory
and restart.

## Configuration

### Initial Setup

1. Navigate to **Settings** → **Devices & Services** in Home Assistant
2. Click **+ Add Integration** and search for **"Quilt"**
3. Enter your Quilt account email address
4. Check your email for a 6-digit verification code (arrives within 1-2 minutes)
5. Enter the code in Home Assistant

**Note:** Verification codes expire after 15 minutes. Request a new code if needed.

If your account has **multiple homes**, you will be prompted to select which one to
integrate. Each home can be added as a separate integration entry — just run the setup
flow again and select a different home.

### Configuration Options

After setup, click **Configure** on the integration card to adjust settings:

| Option | Default | Range | Description |
|---|---|---|---|
| **Polling interval** | 5 min | 1–60 min | Fallback polling frequency when real-time stream is unavailable |

**About real-time updates:** The integration uses gRPC streaming for instant state changes.
The polling interval is only used as a fallback when the stream is temporarily down.

### Reconfiguration

To change your email or re-authenticate without removing the integration:

1. Go to **Settings** → **Devices & Services**
2. Find your Quilt integration
3. Click **⋮** (three dots menu) → **Reconfigure**
4. Follow the authentication steps

### Re-authentication

If your session expires, Home Assistant will display a re-authentication notification.
Click the notification and follow the prompts to re-enter your email and a new
verification code. Your devices and settings remain unchanged.

## Troubleshooting

### "Cannot connect" Error During Setup
- **Check internet connection:** Verify your Home Assistant instance can access the internet
- **Verify Quilt cloud status:** Ensure the Quilt cloud service is online
- **Network/firewall:** Confirm gRPC traffic (HTTPS port 443) is not blocked
- **Temporary outage:** Wait 2-3 minutes and try again

### "Invalid passcode" Error
- **Double-check digits:** Ensure all 6 digits were entered correctly
- **Code expired:** Codes expire after 15 minutes - request a new one
- **Email delays:** Check spam/junk folder if code doesn't arrive within 2 minutes
- **Resend:** Start the setup again to receive a new code

### Connection Lost / Stream Degraded Warning
If you see a repair notification about stream degradation:
- The integration automatically falls back to polling every 5 minutes
- State updates may be delayed but will continue working
- The stream reconnects automatically when connectivity is restored
- **Action:** Check your internet connection; no manual intervention required

### Entities Show as "Unavailable"
- **Device offline:** Verify your Quilt indoor unit has power and WiFi connection
- **Recent setup:** Allow 1-2 minutes after initial setup for devices to come online
- **Cloud connection:** Check that the Quilt app works on your phone
- **Reload integration:** Try reloading from **Devices & Services**

### Integration Won't Load / Setup Fails
1. **Check logs:** Go to **Settings** → **System** → **Logs**, search for "quilt"
2. **Re-authenticate:** Remove and re-add the integration
3. **Restart Home Assistant:** Sometimes resolves temporary issues
4. **Report issue:** File a bug report on GitHub with relevant log entries

### Debugging

To enable debug logging, add to `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.quilt_hp: debug
```

Then restart Home Assistant and check **Settings** → **System** → **Logs** for detailed information.

## Entities

The following entities are created per device. Entities marked *Disabled* are off by
default to reduce noise; enable them individually in the HA entity registry.

**Device Naming:** Indoor unit devices are named using their configured name (e.g., "Living Room IDU") or a fallback pattern like "{Room} Indoor Unit". This ensures device names clearly identify the physical hardware while the room is assigned as the area.

### Per location (home)

| Entity | Platform | Default | Notes |
|---|---|---|---|
| Schedules | `switch` | Enabled | Pauses or resumes all Quilt schedules for the home. Off = paused. |

### Per space (room) — on the IDU device

| Entity | Platform | Default |
|---|---|---|
| Climate | `climate` | Enabled |
| Space temperature | `sensor` | Enabled |

The climate entity supports the following HVAC modes: **Off**, **Cool**, **Heat**,
**Heat/Cool** (auto), **Fan only**, **Dry**.

### Per Indoor Unit (IDU)

The **fan** entity models Quilt's *Auto* fan mode as "off": when the fan entity is off,
the HVAC system controls the fan speed automatically. Turning the fan on engages an
explicit speed (Quiet/Low/Medium/High/Blast, also available as preset modes), and
turning it off returns the fan to automatic control.

| Entity | Platform | Default |
|---|---|---|
| Fan | `fan` | Enabled |
| LED | `light` | Enabled |
| Louver mode | `select` | Enabled |
| Louver angle | `select` | Enabled |
| Temperature (IDU sensor) | `sensor` | Enabled |
| Humidity | `sensor` | Enabled |
| Fan speed | `sensor` | Enabled |
| Motion | `binary_sensor` | Enabled |
| Presence | `binary_sensor` | Enabled |
| Occupied | `binary_sensor` | Enabled |
| Inlet temperature | `sensor` | Disabled |
| Outlet temperature | `sensor` | Disabled |
| Presence level | `sensor` | Disabled |
| HVAC capacity | `sensor` | Disabled |
| HVAC power | `sensor` | Disabled |
| Coefficient of performance | `sensor` | Disabled |
| Calibrated temperature | `sensor` | Disabled |
| Motion signal (radar) | `sensor` | Disabled |
| Presence signal (radar) | `sensor` | Disabled |
| Illuminance | `sensor` | Disabled |
| Online | `binary_sensor` | Disabled |

### Per Outdoor Unit (ODU)

| Entity | Platform | Default |
|---|---|---|
| Outdoor temperature | `sensor` | Enabled |
| Compressor frequency | `sensor` | Disabled |
| High-side pressure | `sensor` | Disabled |
| Low-side pressure | `sensor` | Disabled |

### Per Controller (Quilt Dial)

| Entity | Platform | Default |
|---|---|---|
| Temperature | `sensor` | Enabled |
| Online | `binary_sensor` | Disabled |
| Wi-Fi signal | `sensor` | Disabled |

## Troubleshooting

### Enable debug logging

Add the following to your `config/configuration.yaml` and restart Home Assistant:

```yaml
logger:
  default: warning
  logs:
    custom_components.quilt_hp: debug
```

Logs are written to `home-assistant.log` in your HA config directory.

### Common issues

**Entities become unavailable after a few hours**
The gRPC stream disconnected and the reconnect failed. Check debug logs for
`NotifierStream` errors. Ensure your HA host has outbound internet access to the Quilt
cloud API.

**Re-authentication loop**
Your refresh token has expired. Complete the re-auth flow from the HA notification. If
the issue persists, remove and re-add the integration.

**OTP not accepted**
The OTP expires quickly. Make sure you enter it within a minute or two of receiving it.
If the config flow times out between the email and OTP steps, restart the flow.

### Filing issues

Please open an issue at [github.com/eman/homeassistant-quilt-hp/issues](https://github.com/eman/homeassistant-quilt-hp/issues)
and include:
- Home Assistant version
- Integration version
- Relevant lines from `home-assistant.log` with debug logging enabled

## Use cases

- **Room-by-room comfort** — each Quilt space is a `climate` entity with its own
  setpoints and mode, so bedrooms, offices, and living areas can follow different
  targets and schedules.
- **Presence-aware HVAC** — every indoor unit exposes radar-based `motion`, `presence`,
  and `occupied` binary sensors. Use them to set back unoccupied rooms or as
  general-purpose occupancy sensors for lighting and security automations.
- **Energy tracking** — the per-room *Energy today* sensors plug directly into HA's
  Energy dashboard (add them as individual devices) and reset at local midnight.
- **Schedule control** — the per-home *Schedules* switch pauses or resumes all Quilt
  schedules at once, e.g. while on vacation or during a party.
- **Ambient signals** — the indoor unit LED is a full RGBW `light` with effects; use it
  as a subtle notification surface in rooms that have one.

## Automation examples

Entity IDs below follow HA's generated names (`climate.family_room`,
`binary_sensor.family_room_occupied`, `switch.<home>_schedules`, …) — adjust to yours.

**Set back an empty room, restore it when someone returns:**

```yaml
automation:
  - alias: "Family room away setback"
    triggers:
      - trigger: state
        entity_id: binary_sensor.family_room_occupied
        to: "off"
        for: "00:30:00"
    actions:
      - action: climate.set_temperature
        target:
          entity_id: climate.family_room
        data:
          target_temp_low: 17
          target_temp_high: 27

  - alias: "Family room comfort on return"
    triggers:
      - trigger: state
        entity_id: binary_sensor.family_room_occupied
        to: "on"
    actions:
      - action: climate.set_temperature
        target:
          entity_id: climate.family_room
        data:
          target_temp_low: 20
          target_temp_high: 24
```

**Pause all Quilt schedules while nobody is home:**

```yaml
automation:
  - alias: "Pause Quilt schedules when away"
    triggers:
      - trigger: state
        entity_id: zone.home
        to: "0"
    actions:
      - action: switch.turn_off
        target:
          entity_id: switch.home_schedules
  - alias: "Resume Quilt schedules on arrival"
    triggers:
      - trigger: numeric_state
        entity_id: zone.home
        above: 0
    actions:
      - action: switch.turn_on
        target:
          entity_id: switch.home_schedules
```

**Turn the room off entirely at night, on in the morning:**

```yaml
automation:
  - alias: "Dining room off overnight"
    triggers:
      - trigger: time
        at: "23:00:00"
    actions:
      - action: climate.turn_off
        target:
          entity_id: climate.dining_room
  - alias: "Dining room on in the morning"
    triggers:
      - trigger: time
        at: "06:30:00"
    actions:
      - action: climate.turn_on
        target:
          entity_id: climate.dining_room
```

**Notify when a room's daily energy exceeds a budget:**

```yaml
automation:
  - alias: "Primary bedroom energy budget"
    triggers:
      - trigger: numeric_state
        entity_id: sensor.primary_bedroom_energy_today
        above: 5   # kWh
    actions:
      - action: notify.mobile_app_phone
        data:
          message: >-
            Primary bedroom has used
            {{ states('sensor.primary_bedroom_energy_today') }} kWh today.
```

**Use the indoor unit LED as a doorbell flash:**

```yaml
automation:
  - alias: "Flash family room LED on doorbell"
    triggers:
      - trigger: state
        entity_id: binary_sensor.doorbell
        to: "on"
    actions:
      - action: light.turn_on
        target:
          entity_id: light.family_room_led
        data:
          rgbw_color: [255, 120, 0, 0]
          brightness: 255
          effect: sparkle_fade
      - delay: "00:00:10"
      - action: light.turn_off
        target:
          entity_id: light.family_room_led
```

## Removing the integration

1. In Home Assistant go to **Settings → Devices & services → Quilt**.
2. Open the entry menu (⋮) and select **Delete**.
3. If no other Quilt entry uses the same account, the cached authentication tokens
   are removed from HA storage automatically.

To fully sign the host out of your Quilt account, also revoke the session from the
Quilt mobile app if desired.

## Contributing

### Development setup

```bash
git clone https://github.com/eman/homeassistant-quilt-hp
cd homeassistant-quilt-hp
pip install -r requirements-dev.txt
```

### Running tests and checks

```bash
pytest                        # run all tests
ruff check . && ruff format --check .   # lint + format
mypy custom_components/quilt_hp         # type check
basedpyright custom_components/quilt_hp # type check
```

### Local Home Assistant instance

A `docker-compose.yml` is included for running a real HA instance against the
integration locally:

```bash
docker compose up -d       # start
docker compose restart     # pick up integration changes
docker compose down        # stop
```

HA is available at **http://localhost:8124**.

## Dependencies

- [`quilt-hp-python`](https://pypi.org/project/quilt-hp-python/) — installed automatically by HA.

## License

MIT License. See [LICENSE](LICENSE) for details.

This project is not affiliated with or endorsed by Quilt.
