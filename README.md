# Tuya-to-MQTT Bridge for EG4 Hybrid Solar Mini-Split Heat Pump

A Rust application that connects locally to an EG4 Hybrid Solar Mini-Split Heat Pump's Tuya WiFi module and bridges all data points to MQTT — including hidden solar/energy DPs that Tuya doesn't expose through their standard integrations.

> **WARNING:** This project was largely created with the assistance of AI (Claude). It is provided as-is with no warranty. Use at your own risk. The author is not liable for any damage to your equipment, loss of data, voided warranties, or any other consequences of using this software. Follow best practices when deploying containers and services in your own homelab environment.

## Why This Exists

The EG4 Hybrid Solar Mini-Split ships with a **Tuya WBR3 WiFi+BLE module** for cloud control via the Tuya/Smart Life app. While Tuya's cloud API and integrations like LocalTuya can control basic functions (power, mode, fan speed, temperature), they have significant limitations:

- **LocalTuya couldn't map all DPs** the way we wanted for Home Assistant
- **The solar/energy data points are completely hidden** from Tuya's cloud API and standard integrations
- **No flexibility** in how entities are created in Home Assistant

This bridge connects directly to the device over your local network (no cloud dependency), reads *all* data points including the hidden ones, and publishes raw values to MQTT where you have full control over entity creation.

## Architecture

```
                   Tuya Local Protocol v3.3
┌──────────────┐      TCP:6668        ┌──────────────┐       MQTT       ┌──────────┐
│  WBR3 Module │ ◄──────────────────► │ tuya-to-mqtt │ ◄──────────────► │  Broker  │
│  (Heat Pump) │   AES-128-ECB        │  (this app)  │                  │          │
└──────────────┘                      └──────────────┘                  └──────────┘
```

The bridge:
1. Connects to the WBR3 module over your LAN using Tuya's local protocol (version 3.3, AES-128-ECB encrypted, port 6668)
2. Queries all data points on startup, then listens for real-time updates
3. Publishes each DP value to an MQTT topic using the DP's code name
4. Subscribes to MQTT command topics so you can control the device
5. Only publishes when a value actually changes (change detection)
6. Reconnects automatically with exponential backoff if the connection drops

## Setup

### Prerequisites

- Your heat pump's **device ID** and **local key** from the Tuya cloud API (use [tinytuya](https://github.com/jasonacox/tinytuya) to extract these)
- The device's **local IP address** on your network
- An **MQTT broker** (Mosquitto, EMQX, etc.)
- **Docker** (recommended) or **Rust toolchain** (for building from source)

### 1. Get Your Device Credentials

```bash
pip install tinytuya
python -m tinytuya wizard
```

Follow the prompts to link your Tuya developer account. This will generate a `devices.json` file with your device ID, local key, and DP mapping.

### 2. Create Your Config

Copy the example and fill in your credentials:

```bash
cp devices.json.example devices.json
```

Edit `devices.json`:
- Replace `YOUR_DEVICE_ID` with your actual device ID
- Replace `YOUR_LOCAL_KEY` with your actual local key
- Replace `192.168.1.100` with your heat pump's IP address

Or if you ran `tinytuya wizard`, copy its output and add the `"ip"` field.

### 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your MQTT broker details:
```
MQTT_BROKER_HOST=your-mqtt-broker.local
MQTT_BROKER_PORT=1883
MQTT_USERNAME=your_mqtt_user
MQTT_PASSWORD=your_mqtt_pass
```

### 4. Run

**With Docker (recommended):**

Update `docker-compose.yml` to use the pre-built image:
```yaml
services:
  tuya-to-mqtt:
    image: ghcr.io/tfoote000/eg4heatpumptuyatomqtt:latest
    env_file:
      - .env
    volumes:
      - ./devices.json:/app/devices.json:ro
    working_dir: /app
    restart: unless-stopped
    network_mode: host
```

Then run:
```bash
docker compose up -d
```

**From source:**
```bash
cargo run
```

### 5. Verify

```bash
# Watch all topics
mosquitto_sub -h your-broker -t "tuya/#" -v
```

You should see state topics appearing as the device reports its data points.

## MQTT Topics

```
tuya/{device_id}/status              → "online" or "offline"
tuya/{device_id}/state/{dp_code}     → current value
tuya/{device_id}/command/{dp_code}   → publish here to send commands
```

**Example state topics:**
```
tuya/abc123/state/switch           → "true"
tuya/abc123/state/temp_set         → "22"
tuya/abc123/state/temp_current     → "21"
tuya/abc123/state/mode             → "heat"
tuya/abc123/state/fan_speed_enum   → "auto"
tuya/abc123/state/work_status      → "heating"
tuya/abc123/state/solar_power      → "847"
tuya/abc123/state/grid_power       → "312"
tuya/abc123/state/solar_percent    → "73"
tuya/abc123/state/total_energy     → "1640523"
```

**Example commands:**
```bash
# Turn off via mode (Home Assistant style)
mosquitto_pub -t "tuya/abc123/command/mode" -m "off"

# Set to cooling mode (also turns unit on)
mosquitto_pub -t "tuya/abc123/command/mode" -m "cool"

# Set temperature (accepts integers or floats)
mosquitto_pub -t "tuya/abc123/command/temp_set_f" -m "72"

# Set fan speed
mosquitto_pub -t "tuya/abc123/command/fan_speed_enum" -m "medium"
```

### Home Assistant Integration

The bridge automatically converts between Home Assistant's HVAC values and Tuya's device values:

| DP | HA Value | Tuya Value |
|----|----------|------------|
| mode | `off` | switch = false |
| mode | `cool` | `cold` + switch = true |
| mode | `heat` | `hot` + switch = true |
| mode | `fan_only` | `wind` + switch = true |
| mode | `auto` | `auto` + switch = true |
| fan_speed_enum | `medium` | `mid` |

Setting mode to anything other than "off" automatically turns the unit on. Setting mode to "off" turns the unit off via the switch DP. State topics publish HA-compatible values (e.g., `state/mode` reports `"cool"` not `"cold"`).

## Complete DP Reference

### Official DPs (from Tuya cloud)

| DP | Code | Type | Values | Description |
|----|------|------|--------|-------------|
| 1 | `switch` | Boolean | true/false | Power on/off |
| 2 | `temp_set` | Integer | 16-32 | Target temperature (C) |
| 3 | `temp_current` | Integer | -20 to 100 | Current temperature (C, signed) |
| 4 | `mode` | Enum | off, auto, cool, heat, fan_only | HVAC mode (HA-compatible) |
| 6 | `mode_eco` | Boolean | true/false | Eco mode |
| 9 | `anion` | Boolean | true/false | Ionizer |
| 10 | `heat` | Boolean | true/false | Auxiliary/compressor heat active |
| 11 | `light` | Boolean | true/false | Display LED on/off |
| 19 | `temp_set_f` | Integer | 61-90 | Target temperature (F) |
| 20 | `temp_current_f` | Integer | -4 to 212 | Current temperature (F) |
| 21 | `temp_unit_convert` | Enum | c, f | Temperature unit |
| 22 | `work_status` | Enum | off, cooling, heating, ventilation | Operating status |
| 23 | `fan_speed_enum` | Enum | auto, low, medium, high | Fan speed (HA-compatible) |
| 24 | `fault` | Bitmap | sensor_fault, temp_fault | Fault flags |
| 101 | `sleep` | Boolean | true/false | Sleep mode |

### Unofficial DPs (discovered via UART reverse engineering)

| DP | Code | Type | Unit | Description |
|----|------|------|------|-------------|
| 106 | `solar_power` | Integer | W | Real-time solar power input |
| 107 | `solar_energy` | Integer | Wh | Lifetime solar energy counter |
| 108 | `solar_percent` | Integer | % | Solar percentage of total power |
| 109 | `grid_percent` | Integer | % | Grid percentage of total power |
| 110 | `total_energy` | Integer | Wh | Lifetime total energy counter |
| 111 | `grid_power` | Integer | W | Real-time grid power draw |

> **Note:** The unofficial DPs may or may not be accessible through the Tuya local protocol (they were discovered on the UART bus). The bridge will attempt to query them — if the device responds, they'll appear on MQTT. If not, you'll see a log message and the official DPs will still work.

## Building

```bash
# Debug build
cargo build

# Release build (optimized)
cargo build --release

# Docker build
docker compose build
```

The Docker image supports cross-compilation for `linux/amd64`, `linux/arm64`, and `linux/arm/v7`.

## Project Structure

```
.
├── src/
│   ├── main.rs           # Task orchestration, command routing, shutdown
│   ├── config.rs          # Loads devices.json + environment variables
│   ├── mqtt/
│   │   ├── mod.rs
│   │   └── client.rs      # MQTT client with LWT and change detection
│   └── tuya/
│       ├── mod.rs          # DpUpdate and DpCommand types
│       └── client.rs       # Tuya local protocol client with reconnection
├── reverse-engineering/    # UART captures, analysis scripts, protocol docs
│   ├── PROTOCOL.md         # Complete protocol specification (25 DPs)
│   ├── uart_sniffer.py     # Dual UART capture tool (Raspberry Pi)
│   ├── analyze_capture.py  # Packet decoder and analysis
│   ├── deep_dp_analysis.py # Burst packet handling and DP extraction
│   ├── verify_power_model.py  # Mathematical verification of power metrics
│   ├── ble_uart_correlate.py  # BLE + UART correlation scanner
│   └── uart_capture_*.json # Raw capture data
├── devices.json.example   # Config template with full DP mapping
├── .env.example            # Environment variable template
├── Dockerfile              # Multi-stage build with cargo-chef
└── docker-compose.yml
```

## The Reverse Engineering Journey

### Sniffing the UART Protocol

The WBR3 module communicates with the heat pump's MCU via a serial UART connection at 9600 baud using Tuya's proprietary MCU protocol. We tapped both TX and RX lines using a **Raspberry Pi 3B** with two serial ports:

- **`/dev/ttyAMA0`** (PL011) — tapped the Tuya module's TX line (Tuya -> MCU)
- **`/dev/ttyUSB0`** (USB-Serial adapter) — tapped the MCU's TX line (MCU -> Tuya)

Over **3 capture sessions totaling ~97 minutes**, we decoded approximately **4,063 packets** with a 100% checksum verification rate. The captures covered:

1. Steady heating with active solar, power cycling, mode cycling
2. All 5 HVAC modes, all fan speeds, vane control, light toggle, a full power cycle/init sequence
3. Extended heating as solar declined to zero, C/F unit toggle, swing disable

Every packet follows Tuya's frame format: `55 AA [version] [command] [length] [data] [checksum]`, where version `0x00` = from MCU and `0x03` = from WiFi module.

### Discovering Hidden Data Points

Tuya's cloud API exposes **16 official data points** for this device (power, mode, temperature, fan speed, etc.). But through UART analysis, we discovered **25 total DPs** — including a set of solar/energy DPs that Tuya never documents:

| DP | Name | What It Does |
|----|------|-------------|
| 106 | Solar Power | Real-time solar input in watts |
| 111 | Grid Power | Real-time grid draw in watts |
| 108 | Solar % | Percentage of power from solar |
| 109 | Grid % | Percentage of power from grid |
| 107 | Solar Energy | Cumulative solar energy in Wh (lifetime counter) |
| 110 | Total Energy | Cumulative total energy in Wh (lifetime counter) |
| 105 | Vertical Swing | Vane oscillation enable/disable |
| 112 | Vane Step | Pulse signal that moves the vane one position |
| 119 | Transition Timer | Compressor protection timer during mode changes |

These DPs are pushed from the Tuya module to the MCU via Tuya's non-standard **CMD 0x22** (record-type DP report), updating every ~3 seconds during operation.

### Where Does the Power Data Come From?

This was one of the most surprising findings. The WBR3 module has **no sensors** — only 5 pins (VCC, GND, Enable, TX, RX). Yet it pushes real-time solar and grid power data to the MCU. The data comes from **BLE sub-devices** inside the heat pump.

We confirmed this by running a simultaneous BLE scan + UART capture from the Raspberry Pi. We found **4 Tuya BLE devices** near the unit:

- The WBR3 itself (acting as a BLE gateway)
- 3 BLE sub-devices (power monitoring modules)

The WBR3 connects to these sub-devices via encrypted BLE GATT (service UUID `1910`), reads their sensor data, and relays it to the MCU over UART. The BLE advertisements are static — the actual data flows over encrypted GATT connections, not advertisements.

### Validating the Energy Claims

EG4 markets this unit as a "solar hybrid" heat pump. We mathematically verified their power tracking across **648+ data points** from all 3 captures:

```
Total Power (W) = DP106 (Solar W) + DP111 (Grid W)
DP108 (Solar %) = round(DP106 / Total * 100)
DP109 (Grid %)  = round(DP111 / Total * 100)
DP108 + DP109   = 100 (always)
```

**Results:**
- Average calculation error: **1.0-2.3%** (caused by sequential DP timing, not model inaccuracy)
- Solar and grid percentages always sum to exactly 100%
- Energy counters are monotonically increasing and consistent across power cycles
- Lifetime readings during capture: **~1,233 kWh solar** / **~1,640 kWh total** = **75.2% solar fraction**

The energy tracking is real and mathematically sound. The small errors we observed are from DPs updating sequentially (not atomically) — by the time you read DP108 (solar %), DP106 (solar W) might have already changed slightly.

### What We Also Learned

- **Temperature DP3 is signed** — it can go negative for sub-zero Celsius readings. Parse as `int32`, not `uint32`.
- **Outdoor temperature is NOT on the UART** — the outdoor sensor connects directly to the outdoor unit's control board.
- **The MCU sends burst packets** — during mode changes, multiple DPs are concatenated in rapid-fire CMD 0x06 packets.
- **CMD 0x07 has a dual role** — it serves as both acknowledgment (echoing MCU reports) and control (delivering commands from app/cloud).

Full protocol documentation is in [reverse-engineering/PROTOCOL.md](reverse-engineering/PROTOCOL.md).

## Acknowledgments

- [tinytuya](https://github.com/jasonacox/tinytuya) — Python library for Tuya local protocol and credential extraction
- [rust-async-tuyapi](https://github.com/FruitieX/rust-async-tuyapi) — Rust async Tuya local protocol client
- [rumqttc](https://github.com/bytebeamio/rumqtt) — Rust MQTT client

## License

This project is licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0). See the [LICENSE](LICENSE) file for full terms.
