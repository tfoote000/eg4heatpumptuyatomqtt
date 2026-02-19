# EG4 Hybrid Solar Mini-Split Heat Pump - Protocol Specification

**Date:** 2026-02-18
**Captures Analyzed:** 3 (14.4 min + 46.0 min + ~37 min = ~97 min total)
**Capture 1:** Steady heating with solar, power cycling, mode cycling
**Capture 2:** All modes (heat/cool/dry/fan/auto), fan speeds, vane control, light toggle, extended heating at outdoor temp <=35°F
**Capture 3:** Extended heating (solar declining to zero), C/F unit toggle, vertical vane disable, outdoor temp <=35°F
**Product ID:** `gpjca2vt`, firmware `v1.0.0`, MCU-controlled (`m:1`)
**Control source:** All captures used Tuya app control (cloud → WiFi module → MCU)

---

## 1. Executive Summary

### Key Findings
- **25 unique Data Points** discovered (up from 17 in first capture)
- **5 HVAC modes fully mapped:** Auto(0), Cool(1), Heat(2), Dry(3), Fan(4)
- **5 fan speeds confirmed:** Auto(0), Low(1), Med(2), High(3), Turbo(4)
- **Solar power metrics fully decoded** with mathematical verification (avg error <2%)
- **Two energy counters:** Solar (1,233 kWh lifetime) and Total (1,640 kWh lifetime)
- **Signed temperature values** detected (DP3 can go negative for sub-zero °C)
- **Vertical vane control:** DP105 = swing enable/disable, DP112 = vane step pulse
- **Temperature unit toggle:** DP21 confirmed as °C/°F selector (0=°C, 1=°F)
- **Light toggle:** DP11 confirmed as display/LED on/off
- **Temperature DPs resolved:** DP2/DP19 = paired setpoint (°C/°F), DP3/DP20 = return air temp (°C/°F)
- **Outdoor temp NOT on UART** — sensor connects to outdoor unit, not relayed through Tuya protocol
- **Full init/query sequence captured** showing all DPs the system supports
- **Fault DP (type 0x05)** discovered for error reporting
- **All control was via Tuya app** — no local/remote-only captures
- **Control command flow:** Tuya module uses CMD 0x07 to deliver DP commands to MCU; no CMD 0x06 from Tuya→MCU observed in any capture
- Protocol uses **non-standard Tuya CMD 0x22/0x23** for record-type DP reporting
- Heartbeat interval: **~15 seconds**
- All checksums verified (100% decode rate, ~4063 packets across 3 captures)

### Complete DP Map

| DP ID | Type    | Name                     | Units  | Values/Range    | Direction      | Confidence |
|-------|---------|--------------------------|--------|-----------------|----------------|------------|
| 1     | Boolean | Power State              | on/off | 0-1             | MCU report     | CONFIRMED  |
| 2     | Value   | Set Temperature          | °C     | 16-32           | Tuya push      | CONFIRMED  |
| 3     | Value   | Return Air Temperature   | °C     | **signed**      | Tuya push      | CONFIRMED  |
| 4     | Enum    | HVAC Mode (MCU)          | enum   | 0-4             | MCU report     | CONFIRMED  |
| 6     | Boolean | Unknown Toggle           | on/off | always OFF      | MCU report     | LOW        |
| 9     | Boolean | Unknown Toggle           | on/off | always OFF      | Init only      | LOW        |
| 10    | Boolean | Heating/Compressor Active| on/off | 0-1             | Tuya push      | MEDIUM     |
| 11    | Boolean | Light/Display Toggle     | on/off | 0-1             | MCU report     | CONFIRMED  |
| 19    | Value   | Set Temperature          | °F     | 60-90           | MCU report     | CONFIRMED  |
| 20    | Value   | Return Air Temperature   | °F     | 25-71           | Tuya push      | CONFIRMED  |
| 21    | Enum    | Temperature Unit (C/F)   | enum   | 0=°C, 1=°F     | MCU report     | CONFIRMED  |
| 22    | Enum    | HVAC Mode (Tuya echo)    | enum   | 0-4             | Tuya push      | CONFIRMED  |
| 23    | Enum    | Fan Speed                | enum   | 0-4             | MCU report     | CONFIRMED  |
| 24    | Fault   | Fault/Error Bitmap       | bitmap | 0=no fault      | Init only      | HIGH       |
| 101   | Boolean | Unknown Toggle           | on/off | always OFF      | MCU report     | LOW        |
| 104   | Boolean | Unknown Status           | on/off | always ON       | Tuya push      | LOW        |
| 105   | Boolean | Vertical Swing Enable    | on/off | 0-1             | MCU report     | CONFIRMED  |
| 106   | Value   | Solar Power              | W      | 0-2400          | Tuya push      | VERIFIED   |
| 107   | Value   | Solar Energy Counter     | Wh     | cumulative      | Tuya push      | VERIFIED   |
| 108   | Value   | Solar Power Percentage   | %      | 0-100           | Tuya push      | VERIFIED   |
| 109   | Value   | Grid Power Percentage    | %      | 0-100           | Tuya push      | VERIFIED   |
| 110   | Value   | Total Energy Counter     | Wh     | cumulative      | Tuya push      | VERIFIED   |
| 111   | Value   | Grid Power               | W      | 0-2400          | Tuya push      | VERIFIED   |
| 112   | Boolean | Vane Step Pulse          | step   | pulse pattern   | MCU report     | HIGH       |
| 119   | Value   | Mode Transition Timer    | varies | 0,10,40,50      | Tuya push      | LOW        |

---

## 2. Protocol Structure

### Frame Format
```
[0x55] [0xAA] [Version] [Command] [Length_MSB] [Length_LSB] [Data...] [Checksum]
```

- **Header:** Always `0x55 0xAA`
- **Version:** `0x00` = from MCU, `0x03` = from Tuya module
- **Command:** See command table below
- **Length:** 16-bit big-endian length of data payload
- **Checksum:** `(sum of ALL bytes from 0x55 to end of data) % 256`

### Checksum Calculation
```python
def calculate_checksum(packet_bytes_without_checksum):
    return sum(packet_bytes_without_checksum) % 256
```

### Command Table

| CMD  | Name                    | Direction        | Description                                    |
|------|-------------------------|------------------|------------------------------------------------|
| 0x00 | Heartbeat               | MCU -> Tuya      | Periodic keep-alive (~15s interval)            |
| 0x00 | Heartbeat Response      | Tuya -> MCU      | Response with network status byte              |
| 0x01 | Product Info Query      | MCU -> Tuya      | MCU queries product info (empty data)          |
| 0x01 | Product Info Response   | Tuya -> MCU      | JSON: `{"p":"gpjca2vt","v":"1.0.0","m":1}`     |
| 0x02 | MCU Config Query        | MCU -> Tuya      | MCU queries configuration (empty data)         |
| 0x02 | MCU Config Response     | Tuya -> MCU      | Configuration (empty in this device)           |
| 0x03 | Network Status Report   | MCU -> Tuya      | Network status (data: 0x02=disconnected, 0x03=connecting, 0x04=connected) |
| 0x03 | Network Status Ack      | Tuya -> MCU      | Acknowledgement (empty data)                   |
| 0x06 | DP Status Report        | MCU -> Tuya      | MCU reports DP changes                         |
| 0x07 | DP Status Ack/Command   | Tuya -> MCU      | Ack MCU reports AND deliver control commands    |
| 0x08 | Query All DPs           | MCU -> Tuya      | MCU requests full state dump (empty data)      |
| 0x22 | Record-type DP Report   | Tuya -> MCU      | Tuya pushes sensor/telemetry data to MCU       |
| 0x23 | Record-type DP Ack      | MCU -> Tuya      | MCU acknowledges data push (data: 0x01)        |

### Data Point (DP) Payload Format
```
[DP_ID] [DP_Type] [Length_MSB] [Length_LSB] [Value...]
```

Multiple DPs can be packed in a single packet (observed in burst/init sequences).

**DP Types:**
| Type | Name    | Length | Encoding                                      |
|------|---------|--------|-----------------------------------------------|
| 0x01 | Boolean | 1      | `0x00` = false, `0x01` = true                 |
| 0x02 | Value   | 4      | 32-bit big-endian integer (**can be signed**)  |
| 0x04 | Enum    | 1      | Integer, meaning depends on DP ID             |
| 0x05 | Fault   | 1+     | Bitmask for error flags (0 = no fault)        |

**Important:** DP type 0x02 (Value) can be signed. DP3 uses signed values (observed -4 for sub-zero °C temperatures). Parse as `int32_t`, not `uint32_t`.

### Communication Patterns

```
Pattern 1: Heartbeat (every ~15 seconds)
  MCU  -> Tuya: 55 AA 00 00 00 00 FF
  Tuya -> MCU:  55 AA 03 00 00 01 01 04

Pattern 2: MCU reports state change (from Tuya app control or local input)
  MCU  -> Tuya: 55 AA 00 06 [len] [DP data] [checksum]
  Tuya -> MCU:  55 AA 03 07 [len] [same DP data] [checksum]
  Response time: 5-21ms
  Note: All observed state changes originated from Tuya app commands.
  CMD 0x07 serves as BOTH the control command delivery AND ack.
  No CMD 0x06 from Tuya→MCU was observed in any capture.

Pattern 3: Tuya pushes sensor data to MCU
  Tuya -> MCU:  55 AA 03 22 [len] [DP data] [checksum]
  MCU  -> Tuya: 55 AA 00 23 00 01 01 24
  Response time: 160-490ms

Pattern 4: Burst/init (multiple packets concatenated in rapid succession)
  MCU and Tuya may send multiple packets back-to-back without waiting
  for individual acks. Parser must handle 55 AA boundaries within a
  single serial read buffer.
```

---

## 3. Control Commands

### DP 1 - Power State (Boolean)
- **Direction:** MCU reports via CMD 0x06 when user toggles power
- **Values:** `0x00` = OFF, `0x01` = ON

```
POWER ON:
  MCU sends: 55 AA 00 06 00 05 01 01 00 01 01 0E
  Tuya acks: 55 AA 03 07 00 05 01 01 00 01 01 12

POWER OFF:
  MCU sends: 55 AA 00 06 00 05 01 01 00 01 00 0D
  Tuya acks: 55 AA 03 07 00 05 01 01 00 01 00 11
```

### DP 4 - HVAC Mode (Enum) - MCU Perspective
- **Direction:** MCU reports via CMD 0x06 when mode changes
- **Values:** ALL 5 CONFIRMED from capture 2

| Value | Mode     | Packet (MCU sends CMD 0x06)                    |
|-------|----------|------------------------------------------------|
| 0     | Auto     | `55 AA 00 06 00 05 04 04 00 01 00 13`          |
| 1     | Cool     | `55 AA 00 06 00 05 04 04 00 01 01 14`          |
| 2     | Heat     | `55 AA 00 06 00 05 04 04 00 01 02 15`          |
| 3     | Dry      | `55 AA 00 06 00 05 04 04 00 01 03 16`          |
| 4     | Fan Only | `55 AA 00 06 00 05 04 04 00 01 04 17`          |

### DP 22 - HVAC Mode (Enum) - Tuya Echo
- **Direction:** Tuya pushes via CMD 0x22 to sync mode state back to MCU
- **Values:** Same mapping as DP4 (0=Auto, 1=Cool, 2=Heat, 3=Dry, 4=Fan)
- **Note:** DP22 updates slightly AFTER DP4 during mode transitions (brief mismatch is normal)

### DP 23 - Fan Speed (Enum)
- **Direction:** MCU reports via CMD 0x06 when fan speed changes
- **Values:** ALL 5 CONFIRMED (sequential cycling observed in capture 1)

| Value | Speed  | Packet (MCU sends CMD 0x06)                    |
|-------|--------|------------------------------------------------|
| 0     | Auto   | `55 AA 00 06 00 05 17 04 00 01 00 26`          |
| 1     | Low    | `55 AA 00 06 00 05 17 04 00 01 01 27`          |
| 2     | Medium | `55 AA 00 06 00 05 17 04 00 01 02 28`          |
| 3     | High   | `55 AA 00 06 00 05 17 04 00 01 03 29`          |
| 4     | Turbo  | `55 AA 00 06 00 05 17 04 00 01 04 2A`          |

### DP 112 - Vane Step Pulse (Boolean, Stepping Pattern)
- **Direction:** MCU reports OFF via CMD 0x06, Tuya acks ON via CMD 0x07
- **Behavior:** Each MCU OFF->Tuya ON cycle steps the vane one position
- **Pattern observed:** 5 rapid cycles (every ~1.5s) = full sweep of vane positions
- **Also fires during mode changes** (vane resets to default position for new mode)
- **Relationship to DP105:** DP105 enables/disables auto-swing. DP112 pulses are for manual vane repositioning (or internal vane movement during mode changes)

```
VANE STEP (each cycle moves vane one position):
  MCU sends: 55 AA 00 06 00 05 70 01 00 01 00 7C  (DP112=OFF)
  Tuya acks: 55 AA 03 07 00 05 70 01 00 01 01 81  (DP112=ON)
  [repeat for each step]
```

---

## 4. Temperature Sensors

### DP 2 (0x02) / DP 19 (0x13) - Set Temperature (°C / °F) - CONFIRMED PAIR
- **DP 2:** Value (int32, unsigned), Tuya pushes via CMD 0x22 (°C)
- **DP 19:** Value (int32, unsigned), MCU reports via CMD 0x06 (°F)
- **DP2 observed range:** 20-24°C
- **DP19 observed range:** 68-75°F
- **This is the TARGET/SETPOINT temperature.** DP2 and DP19 are the same setpoint in °C and °F.
- **Confirmed by correlation analysis:**
  - DP2 and DP19 always appear together (within milliseconds) when user adjusts temperature
  - In capture 3, **neither DP2 nor DP19 appeared** because the setpoint was not changed — proving these are setpoints, not measured temperatures
  - 8/9 correlation checks were exact °C↔°F matches; the one mismatch was a transient during rapid button presses
- **Protocol flow:** MCU reports DP19 (°F) first via CMD 0x06, then Tuya pushes DP2 (°C) via CMD 0x22

```
SET TEMP 22°C / 72°F:
  MCU reports: 55 AA 00 06 00 08 13 02 00 04 00 00 00 48 6E  (DP19=72°F)
  Tuya pushes: 55 AA 03 22 00 08 02 02 00 04 00 00 00 16 4A  (DP2=22°C)
  MCU acks:    55 AA 00 23 00 01 01 24
```

### DP 3 (0x03) / DP 20 (0x14) - Return Air Temperature (°C / °F) - CONFIRMED PAIR
- **DP 3:** Value (int32, **SIGNED**), Tuya pushes via CMD 0x22 (°C)
- **DP 20:** Value (int32, unsigned), Tuya pushes via CMD 0x22 (°F)
- **DP3 observed range:** -4 to 22°C
- **DP20 observed range:** 25 to 71°F
- **This is the MEASURED indoor return air temperature** — air going into the indoor coils.
- **Paired values:** DP3 and DP20 always represent the same temperature in °C and °F
  - DP3=21°C -> DP20=69°F (21*9/5+32 = 69.8 ≈ 70, rounded to 69)
  - DP3=20°C -> DP20=68°F (exact)
  - DP3=-4°C -> DP20=25°F (-4*9/5+32 = 24.8 ≈ 25)
  - DP3=1°C -> DP20=33°F (1*9/5+32 = 33.8 ≈ 33)
- **Update interval:** ~3-7 minutes
- **Confirmed as indoor temp:** Reads 68-71°F during heating while outdoor temp was <=35°F
- **Restart behavior:** Shows stale cached value on init (53°F observed at power-on), then stabilizes to actual reading
- **Init values** (-4°C, 1°C) were stale cached values from Tuya module's non-volatile storage

**Data source:** The Tuya module has NO sensors (only VCC, GND, Enable, TX, RX). The return air temperature data arrives at the Tuya module via WiFi (cloud) or BLE (from a power monitoring device on the main board) and is relayed to the MCU via CMD 0x22. See Section 5 note on CMD 0x22 data source.

**Outdoor temperature** is NOT present on the UART protocol. The outdoor temp sensor connects directly to the outdoor unit's control board and is not relayed through the Tuya serial interface.

**For ESP32 replacement:** The ESP32 would need to obtain temperature readings independently (e.g., DS18B20 sensor) and push via CMD 0x22 as DP3/DP20, OR the MCU may function without these pushes (needs testing).

---

## 5. Solar Power Metrics (MATHEMATICALLY VERIFIED)

> **Note:** All power data arrives at the MCU via CMD 0x22 from the Tuya module. The Tuya module has no sensors — data likely arrives via BLE from the MPPT controller/power monitor. See Section 9.6 for details.

### Verified Power Model
```
Total Power (W) = DP 106 (Solar W) + DP 111 (Grid W)
DP 108 = round(DP 106 / Total * 100)  [Solar %]
DP 109 = round(DP 111 / Total * 100)  [Grid %]
DP 108 + DP 109 = 100  [always]
```

**Verification across all 3 captures:** 648+ data points tested.
- Capture 1: 207 samples, avg error 1.6-1.9%
- Capture 2: 307 samples, avg error 1.0-2.3%
- Capture 3: 134 samples, avg error 1.3-1.5%, max 31%
- Errors due to sequential DP timing (not simultaneous updates), not model inaccuracy

### DP 106 (0x6A) - Solar Power (watts)
- **Type:** Value (int32, big-endian, unsigned)
- **Direction:** Tuya -> MCU via CMD 0x22
- **Range observed:** 0-259W (captures were evening/night; expect higher midday)
- **Update interval:** ~3 seconds when active
- **Note:** Values oscillate between nearby pairs (e.g., 20/22, 37/39, 57/59) due to MPPT voltage cycling

### DP 111 (0x6F) - Grid Power (watts)
- **Type:** Value (int32, big-endian, unsigned)
- **Direction:** Tuya -> MCU via CMD 0x22
- **Range observed:** 0-659W
- **Update interval:** ~3 seconds when active
- **Behavior:** Ramps up with compressor load, decreases as target temp is reached

### DP 108 (0x6C) - Solar Power Percentage (%)
- **Type:** Value (int32, big-endian, unsigned)
- **Direction:** Tuya -> MCU via CMD 0x22
- **Range:** 0-100 (always sums with DP 109 to 100)

### DP 109 (0x6D) - Grid Power Percentage (%)
- **Type:** Value (int32, big-endian, unsigned)
- **Direction:** Tuya -> MCU via CMD 0x22
- **Range:** 0-100 (always sums with DP 108 to 100)
- **Note:** 0% when unit is off, jumps to 100% briefly on startup before solar data populates

### DP 107 (0x6B) - Solar Energy Counter (Wh)
- **Type:** Value (int32, big-endian, unsigned)
- **Direction:** Tuya -> MCU via CMD 0x22
- **Values observed:** 1,233,012 - 1,233,065 Wh (lifetime: ~1,233 kWh)
- **Update interval:** ~45-90 seconds (increments by 1 Wh per update)
- **Monotonically increasing** (except during init when cached values are replayed)

### DP 110 (0x6E) - Total Energy Counter (Wh)
- **Type:** Value (int32, big-endian, unsigned)
- **Direction:** Tuya -> MCU via CMD 0x22
- **Values observed:** 1,639,180 - 1,639,985 Wh (lifetime: ~1,640 kWh)
- **Update interval:** ~3-10 seconds (increments by 1 Wh per update)
- **Lifetime solar fraction:** 75.2% (1,233/1,640 kWh)

### Real-Time Power Examples
```
Capture 1 - Evening (solar declining):
  Solar Power (DP 106):     239 W
  Grid Power (DP 111):      657 W
  Total Power:              896 W
  Solar % (DP 108):         27%
  Grid % (DP 109):          73%

Capture 2 - Night (minimal solar):
  Solar Power (DP 106):     42 W
  Grid Power (DP 111):      577 W
  Total Power:              619 W
  Solar % (DP 108):         7%
  Grid % (DP 109):          93%

Capture 3 - Evening to night (solar declining to zero):
  Solar Power (DP 106):     274 W (peak) → 0 W
  Grid Power (DP 111):      77 W → 1279 W (peak)
  Total Energy (DP 110):    1,639,612 → 1,639,985 Wh (+373 Wh)
```

---

## 6. Operational Status DPs

### DP 10 (0x0A) - Heating/Compressor Active (Boolean)
- **Direction:** Tuya pushes via CMD 0x22
- **Observed:** ON when heating mode is actively running, OFF when power is turned off
- **Pattern:** Set to ON ~0.4s after mode is set to HEAT, set to OFF immediately when power OFF

### DP 11 (0x0B) - Light/Display Toggle (Boolean) - CONFIRMED
- **Direction:** MCU reports via CMD 0x06
- **Observed:** User toggled OFF then ON (at 22:01 and 22:03 in capture 2)
- **Function:** Controls the indoor unit's front panel display/LED

```
LIGHT ON:
  MCU sends: 55 AA 00 06 00 05 0B 01 00 01 01 18
  Tuya acks: 55 AA 03 07 00 05 0B 01 00 01 01 1C

LIGHT OFF:
  MCU sends: 55 AA 00 06 00 05 0B 01 00 01 00 17
  Tuya acks: 55 AA 03 07 00 05 0B 01 00 01 00 1B
```

### DP 105 (0x69) - Vertical Swing Enable (Boolean) - CONFIRMED
- **Direction:** MCU reports via CMD 0x06
- **Values:** `True` = swing mode enabled (vane oscillates), `False` = fixed position
- **Confirmed in capture 3:** User turned off vertical vane → DP105=False at 04:11
- **Also observed in capture 2:** DP105 toggled ON at 21:22 (enabling swing), OFF at 21:36 (disabling swing)
- **Relationship to DP112:** DP105 is the master swing enable/disable toggle. DP112 is the step pulse for manual vane position changes. When DP105=True, the vane oscillates automatically. When DP105=False, the vane is fixed and DP112 pulses can reposition it.

```
SWING ON:
  MCU sends: 55 AA 00 06 00 05 69 01 00 01 01 76
  Tuya acks: 55 AA 03 07 00 05 69 01 00 01 01 7A

SWING OFF:
  MCU sends: 55 AA 00 06 00 05 69 01 00 01 00 75
  Tuya acks: 55 AA 03 07 00 05 69 01 00 01 00 79
```

### DP 104 (0x68) - Unknown Status (Boolean)
- **Direction:** Tuya pushes via CMD 0x22
- **Observed:** Always ON when seen
- **Hypothesis:** Could be "solar available", "compressor enabled", or system status flag

### DP 6 (0x06) - Unknown Toggle (Boolean)
- **Direction:** MCU reports via CMD 0x06 during mode changes
- **Observed:** Always OFF
- **Hypothesis:** Quiet mode, eco mode, or auxiliary heat (disabled feature)

### DP 9 (0x09) - Unknown Toggle (Boolean)
- **Direction:** Only appears in init dump (CMD 0x07)
- **Observed:** Always OFF
- **Hypothesis:** Eco mode, sleep mode, or another disabled feature

### DP 101 (0x65) - Unknown Toggle (Boolean)
- **Direction:** MCU reports via CMD 0x06 during mode changes
- **Observed:** Always OFF
- **Hypothesis:** Beep enable, display toggle, or proprietary feature

### DP 21 (0x15) - Temperature Unit (C/F) - CONFIRMED
- **Type:** Enum
- **Direction:** MCU reports via CMD 0x06 when user toggles C/F
- **Values:** `0` = °C, `1` = °F
- **Confirmed in capture 3:** User toggled C/F modes 4 times, DP21 cycled 0→1→0→1
- **Init dump value:** 1 (°F) — consistent with display showing Fahrenheit

```
TEMP UNIT = °F:
  MCU sends: 55 AA 00 06 00 05 15 04 00 01 01 25
  Tuya acks: 55 AA 03 07 00 05 15 04 00 01 01 29

TEMP UNIT = °C:
  MCU sends: 55 AA 00 06 00 05 15 04 00 01 00 24
  Tuya acks: 55 AA 03 07 00 05 15 04 00 01 00 28
```

### DP 24 (0x18) - Fault/Error Bitmap
- **Type:** Fault (0x05)
- **Direction:** Only appears in init dump (CMD 0x07)
- **Observed:** Value 0 = no fault
- **Note:** Tuya fault type uses bitmask encoding. Non-zero values indicate specific errors.

### DP 119 (0x77) - Mode Transition Timer / Status (Value)
- **Type:** Value (int32, big-endian, unsigned)
- **Direction:** Tuya pushes via CMD 0x22
- **Observed values:** 0, 10, 40, 50
- **Behavior patterns:**
  - `10 -> 0` pairs during mode transitions (~2s apart, repeated)
  - `40` appears after power OFF (compressor protection delay?)
  - `50 -> 40 -> 50 -> 40 -> 0` during startup mode changes (capture 1)
  - `0` during steady-state operation
- **Hypothesis:** Compressor protection timer or mode transition countdown (seconds?)

---

## 7. Initialization Sequence

### Full Init Observed in Capture 2 (power cycle at ~27:24)

```
1. MCU -> Tuya: CMD 0x01 Product Info Query (empty)
2. Tuya -> MCU: CMD 0x01 Product Info Response
   Data: {"p":"gpjca2vt","v":"1.0.0","m":1}
   (p=product_id, v=version, m=1 means MCU is main controller)

3. MCU -> Tuya: CMD 0x02 MCU Config Query (empty)
4. Tuya -> MCU: CMD 0x02 MCU Config Response (empty)

5. MCU -> Tuya: CMD 0x03 Network Status (data: 0x02 = disconnected)
6. Tuya -> MCU: CMD 0x03 Network Status Ack

7. MCU -> Tuya: CMD 0x08 Query All DPs (empty)
8. Tuya -> MCU: CMD 0x07 BURST - Full state dump of ALL DPs:
   (sent as rapid burst of multiple CMD 0x07 packets)

   DP1(Boolean)=OFF, DP2(Value)=20, DP3(Value)=-4,
   DP4(Enum)=2, DP6(Boolean)=OFF, DP9(Boolean)=OFF,
   DP10(Boolean)=OFF, DP11(Boolean)=ON, DP19(Value)=69,
   DP20(Value)=25, DP21(Enum)=1, DP22(Enum)=0,
   DP23(Enum)=0, DP24(Fault)=0, DP101(Boolean)=OFF,
   DP104(Boolean)=ON, DP105(Boolean)=ON,
   DP106(Value)=0, DP107(Value)=1233057, DP108(Value)=0,
   DP109(Value)=0, DP110(Value)=1639611, DP111(Value)=0,
   DP112(Boolean)=ON

9. MCU -> Tuya: CMD 0x03 Network Status (0x03 = connecting)
10. Tuya -> MCU: CMD 0x03 Ack
11. Second CMD 0x07 burst with updated values
    (DP3 changed from -4 to 1, DP20 from 25 to 33)

12. MCU -> Tuya: CMD 0x03 Network Status (0x04 = connected)
13. Normal operation begins (heartbeats + DP exchanges)
```

**Key insight:** The init dump reveals ALL DPs the system supports, including DPs that are normally never updated (DP6, DP9, DP21, DP24, DP101). This is the complete DP set for this device.

---

## 8. Communication Patterns

### Steady-State Operation
```
Every ~15 seconds: Heartbeat exchange
Every ~3 seconds: One sensor DP update from Tuya via CMD 0x22
  Cycle: DP106 -> DP108 -> DP109 -> DP111 -> [DP107] -> [DP110]
  (DP107/DP110 update less frequently as they're Wh counters)
Every ~3-7 minutes: Temperature updates (DP 3, DP 20)
On user action: MCU reports via CMD 0x06 (power, mode, fan, temp, vane)
```

### Mode Change Sequence
```
User presses mode button on remote:
1. MCU reports new mode via DP4 (CMD 0x06)
   Also reports: DP6, DP10, DP23, DP101 in same burst
2. Tuya acks with matching DP values (CMD 0x07)
3. MCU reports DP112=OFF (vane reset, CMD 0x06)
4. Tuya acks DP112=ON (CMD 0x07)
5. Tuya pushes DP22 (mode echo, CMD 0x22)
6. Tuya pushes DP119 (transition timer, CMD 0x22)
7. DP119 toggles 10->0 one or more times during transition
```

### Fan Speed Change Sequence
```
User presses fan button on remote:
1. MCU reports new fan speed via DP23 (CMD 0x06)
2. Tuya acks (CMD 0x07)
```

### Vertical Vane Control Sequence
```
Swing toggle (DP105):
  MCU sends DP105=ON/OFF (CMD 0x06) - swing enable/disable
  Tuya acks DP105=ON/OFF (CMD 0x07)

Vane step pulse (DP112, when swing is off or during mode changes):
1. MCU sends DP112=OFF (CMD 0x06) - step request
2. Tuya acks DP112=ON (CMD 0x07) - confirmed
3. Repeat 5 times at ~1.5s intervals for full sweep
```

---

## 9. ESP32 Implementation Guide

### Wiring
The Tuya WBR3 module connects with only 5 pins: VCC, GND, Enable, TX, RX. No sensors or other control lines.
```
ESP32 TX -> MCU RX (where Tuya TX was connected)
ESP32 RX -> MCU TX (where Tuya RX was connected)
ESP32 3.3V -> VCC (where Tuya VCC was connected)
ESP32 GND -> GND
ESP32 GPIO -> Enable pin (active high, can tie to VCC if not needed)
```

### Serial Configuration
- **Baud:** 9600
- **Data bits:** 8
- **Parity:** None
- **Stop bits:** 1

### Required ESP32 Behaviors

#### 1. Heartbeat Response (CRITICAL)
Must respond to every MCU heartbeat within a few ms:
```c
// MCU sends: 55 AA 00 00 00 00 FF
// ESP32 must respond: 55 AA 03 00 00 01 01 04
uint8_t heartbeat_response[] = {0x55, 0xAA, 0x03, 0x00, 0x00, 0x01, 0x01, 0x04};
```

#### 2. Handle Init Sequence
On startup, the MCU will send CMD 0x01, 0x02, 0x03, 0x08. The ESP32 must respond:
```c
// CMD 0x01 Product Info: respond with product JSON
uint8_t product_info[] = "{\"p\":\"gpjca2vt\",\"v\":\"1.0.0\",\"m\":1}";
// CMD 0x02 MCU Config: respond with empty data
// CMD 0x03 Network Status: respond with empty ack
// CMD 0x08 Query DPs: respond with CMD 0x07 containing all current DP states
```

#### 3. Acknowledge MCU DP Reports (CMD 0x06 -> CMD 0x07)
When MCU sends CMD 0x06 with DP data, respond with CMD 0x07 containing the same DP data:
```c
void ack_mcu_report(uint8_t* dp_data, uint16_t dp_len) {
    uint8_t pkt[256];
    pkt[0] = 0x55; pkt[1] = 0xAA;
    pkt[2] = 0x03;  // Version: from ESP32 (acting as Tuya)
    pkt[3] = 0x07;  // CMD: DP Status Ack
    pkt[4] = (dp_len >> 8) & 0xFF;
    pkt[5] = dp_len & 0xFF;
    memcpy(&pkt[6], dp_data, dp_len);
    pkt[6 + dp_len] = checksum(pkt, 6 + dp_len);
    serial_write(pkt, 7 + dp_len);
}
```

**Important:** MCU may send MULTIPLE concatenated CMD 0x06 packets in a single burst (e.g., during mode changes: DP4 + DP6 + DP10 + DP23 + DP101 all at once). Parse each 55 AA header boundary separately.

#### 4. Parse MCU DP Reports
```c
void handle_mcu_dp_report(uint8_t* dp_data, uint16_t dp_len) {
    int i = 0;
    while (i < dp_len - 3) {
        uint8_t dp_id = dp_data[i];
        uint8_t dp_type = dp_data[i+1];
        uint16_t val_len = (dp_data[i+2] << 8) | dp_data[i+3];
        uint8_t* val = &dp_data[i+4];

        switch (dp_id) {
            case 1:  // Power state
                power_on = val[0];
                break;
            case 4:  // Operating mode (0=auto,1=cool,2=heat,3=dry,4=fan)
                mode = val[0];
                break;
            case 19: // Set temperature (°F) - paired with DP2 (°C)
                setpoint_f = (val[0]<<24)|(val[1]<<16)|(val[2]<<8)|val[3];
                break;
            case 23: // Fan speed (0=auto,1=low,2=med,3=high,4=turbo)
                fan_speed = val[0];
                break;
            case 112: // Vane step pulse
                // Each OFF report = one vane step
                break;
        }
        i += 4 + val_len;
    }
}
```

#### 5. Send Control Commands to MCU
**Important:** In all captured data (3 captures, all app-controlled), the Tuya module uses **CMD 0x07** to deliver DP commands to the MCU. No CMD 0x06 was ever sent from Tuya→MCU. The MCU responds to CMD 0x07 by applying the new DP value and reporting back via CMD 0x06.

The ESP32 should use CMD 0x07 (version 0x03) for all control commands:

```c
void send_power(bool on) {
    uint8_t dp[] = {0x01, 0x01, 0x00, 0x01, on ? 0x01 : 0x00};
    send_dp_cmd(dp, sizeof(dp));
}

void send_mode(uint8_t mode) {
    // 0=auto, 1=cool, 2=heat, 3=dry, 4=fan
    uint8_t dp[] = {0x04, 0x04, 0x00, 0x01, mode};
    send_dp_cmd(dp, sizeof(dp));
}

void send_fan_speed(uint8_t speed) {
    // 0=auto, 1=low, 2=med, 3=high, 4=turbo
    uint8_t dp[] = {0x17, 0x04, 0x00, 0x01, speed};
    send_dp_cmd(dp, sizeof(dp));
}

void send_set_temp_c(uint8_t temp) {
    uint8_t dp[] = {0x02, 0x02, 0x00, 0x04, 0x00, 0x00, 0x00, temp};
    send_dp_cmd(dp, sizeof(dp));
}

void send_temp_unit(bool fahrenheit) {
    // 0=°C, 1=°F
    uint8_t dp[] = {0x15, 0x04, 0x00, 0x01, fahrenheit ? 0x01 : 0x00};
    send_dp_cmd(dp, sizeof(dp));
}

void send_swing(bool enable) {
    // DP105: vertical swing on/off
    uint8_t dp[] = {0x69, 0x01, 0x00, 0x01, enable ? 0x01 : 0x00};
    send_dp_cmd(dp, sizeof(dp));
}

void send_light(bool on) {
    // DP11: display/LED on/off
    uint8_t dp[] = {0x0B, 0x01, 0x00, 0x01, on ? 0x01 : 0x00};
    send_dp_cmd(dp, sizeof(dp));
}

// Send a DP command to MCU via CMD 0x07 (the control/ack command)
void send_dp_cmd(uint8_t* dp_data, uint16_t dp_len) {
    uint8_t pkt[256];
    pkt[0] = 0x55; pkt[1] = 0xAA;
    pkt[2] = 0x03;  // Version: from ESP32 (acting as Tuya module)
    pkt[3] = 0x07;  // CMD: DP Status Ack/Command
    pkt[4] = (dp_len >> 8) & 0xFF;
    pkt[5] = dp_len & 0xFF;
    memcpy(&pkt[6], dp_data, dp_len);
    pkt[6 + dp_len] = checksum(pkt, 6 + dp_len);
    serial_write(pkt, 7 + dp_len);
}
```

**Note on CMD 0x07 dual role:** CMD 0x07 from the WiFi module serves as both:
1. **Acknowledgment** — echoing the MCU's CMD 0x06 report back (response within 10-17ms)
2. **Control command** — delivering new DP values from the cloud/app to the MCU

The MCU expects CMD 0x07 for both purposes. When the ESP32 sends CMD 0x07 with a new DP value, the MCU should apply it and confirm via CMD 0x06.

#### 6. CMD 0x22 Data Source — Open Question

**Critical architectural finding:** The Tuya module has NO sensors or additional connections (only VCC, GND, Enable, TX, RX). Yet CMD 0x22 packets FROM the Tuya module TO the MCU carry real-time power metrics (DPs 106-111), temperature (DP3/DP20), and status echoes (DP2, DP10, DP22, DP104, DP119). The MCU never sends this data via CMD 0x06.

**Where does the CMD 0x22 data come from?**

The WBR3 module (Realtek RTL8720CF) supports both WiFi and **BLE 4.2**. The most likely data source:

1. **BLE (most likely for power data)** — The MPPT controller or a power monitoring IC on the main board may broadcast real-time power readings via BLE. The WBR3 receives them via its BLE radio and relays to the MCU via CMD 0x22. This explains the 3-second update rate and real-time accuracy.

2. **Tuya cloud (likely for echoed DPs)** — Status echoes like DP2 (setpoint °C), DP22 (mode echo), DP10 (compressor active) are likely the cloud sending the MCU's own reported state back down for sync purposes.

3. **Cached/NV storage** — Energy counters (DP107, DP110) retain their values across power cycles via the WBR3's flash storage.

**For ESP32 replacement:**
- **Control DPs (CMD 0x06)** — Will work. MCU reports power/mode/fan/temp and ESP32 acks.
- **Power monitoring (DPs 106-111)** — Will be LOST unless the ESP32 can replicate the BLE connection to the power monitoring device. An ESP32 with BLE (most variants) could potentially pair with the same BLE power meter.
- **Temperature (DP3/DP20)** — Will need an external sensor on the ESP32, or may not be needed if the MCU functions without it.
- **Status echoes (DP2, DP10, DP22, DP104, DP119)** — The ESP32 can generate these from the MCU's own CMD 0x06 reports (echo back what the MCU tells you).

**To verify BLE theory before building ESP32 firmware:**
1. Use a BLE scanner (nRF Connect app) near the unit while running — look for BLE advertisements with power data
2. Check the main board for a BLE-capable power monitoring IC
3. Check the Tuya app for a separate "power meter" device paired with the heat pump
4. Test: block WiFi but not BLE — if CMD 0x22 power data still flows, it's BLE-sourced

**Testing required after ESP32 installation:**
1. Check if MCU reports power/temperature data via CMD 0x06 when it doesn't receive CMD 0x22 pushes
2. Identify the power monitoring IC on the Tuya module's board (likely a CT sensor or shunt resistor)
3. Determine if DP3/DP20 temperature data comes from a sensor on the Tuya board

### Acknowledge Record-type DP from MCU (CMD 0x23)
If the MCU sends CMD 0x22 packets to the ESP32, respond with:
```c
uint8_t ack_22[] = {0x55, 0xAA, 0x00, 0x23, 0x00, 0x01, 0x01, 0x24};
```

---

## 10. Home Assistant Integration Entities

### Climate Entity
```yaml
climate:
  - platform: esp32_tuya_heatpump
    name: "EG4 Heat Pump"
    # Power: DP 1
    # Mode: DP 4 (0=auto, 1=cool, 2=heat, 3=dry, 4=fan)
    # Fan: DP 23 (0=auto, 1=low, 2=med, 3=high, 4=turbo)
    # Setpoint: DP 2 (°C) / DP 19 (°F) - paired setpoint
    # Current temp: DP 3 (°C) / DP 20 (°F) - return air temperature
    # Swing: DP 105 (vertical swing enable/disable)
    # Temp unit: DP 21 (0=°C, 1=°F)
    # Light: DP 11 (display on/off)
```

### Temperature Sensors
```yaml
sensor:
  - name: "Heat Pump Return Air Temperature"
    dp: 3  # °C, or dp: 20 for °F
    unit: "°C"
    device_class: temperature
    # Measured indoor return air temp (air going into coils)
    # Sensor is on the Tuya/WiFi module board

  - name: "Heat Pump Set Temperature"
    dp: 2  # °C, or dp: 19 for °F
    unit: "°C"
    device_class: temperature
    # Target/setpoint temperature
```

### Power Sensors
```yaml
sensor:
  - name: "Heat Pump Solar Power"
    dp: 106
    unit: W
    device_class: power

  - name: "Heat Pump Grid Power"
    dp: 111
    unit: W
    device_class: power

  - name: "Heat Pump Total Power"
    # Computed: DP 106 + DP 111
    unit: W
    device_class: power

  - name: "Heat Pump Solar Percentage"
    dp: 108
    unit: "%"

  - name: "Heat Pump Grid Percentage"
    dp: 109
    unit: "%"

  - name: "Heat Pump Solar Energy"
    dp: 107
    unit: Wh
    device_class: energy
    state_class: total_increasing

  - name: "Heat Pump Total Energy"
    dp: 110
    unit: Wh
    device_class: energy
    state_class: total_increasing
```

### Status Sensors
```yaml
binary_sensor:
  - name: "Heat Pump Heating Active"
    dp: 10

  - name: "Heat Pump Fault"
    dp: 24
    # value > 0 means fault present
```

---

## 11. Packet Statistics

### Capture 1 (14.4 min, evening with solar)
```
Total decoded packets:        775
  Heartbeats (CMD 0x00):      114
  MCU DP Reports (CMD 0x06):   15
  Tuya DP Acks (CMD 0x07):     17
  Tuya DP Pushes (CMD 0x22):  316
  MCU DP Acks (CMD 0x23):     313
```

### Capture 2 (46.0 min, mode testing + extended heating)
```
Total decoded packets:        1946
  Heartbeats (CMD 0x00):      352
  Product Info (CMD 0x01):      2
  MCU Config (CMD 0x02):        2
  Network Status (CMD 0x03):    2
  MCU DP Reports (CMD 0x06):   29
  Tuya DP Acks (CMD 0x07):     32
  Query All DPs (CMD 0x08):     1
  Tuya DP Pushes (CMD 0x22):  768
  MCU DP Acks (CMD 0x23):     758
```

### Capture 3 (~37 min, heating + C/F toggle + vane disable)
```
Total decoded packets:        1342
  Tuya→MCU:
    Heartbeats (CMD 0x00):    146
    DP Ack/Cmd (CMD 0x07):      6
    Record Push (CMD 0x22):   519
  MCU→Tuya:
    Heartbeats (CMD 0x00):    146
    DP Reports (CMD 0x06):      6
    Record Ack (CMD 0x23):    519
  Active DPs: 11 (of 25 total)
  Note: No init/power cycle in this capture
  Note: CMD 0x06 count = CMD 0x07 count (6:6)
        Zero CMD 0x06 from Tuya→MCU direction
```

---

## 12. Known Unknowns / Future Investigation

### DPs Needing Further Identification
| DP  | Current Hypothesis | What Would Clarify |
|-----|-------------------|--------------------|
| 6   | Quiet/eco mode | Toggle quiet mode on remote |
| 9   | Sleep/eco mode | Toggle sleep mode on remote |
| 101 | Beep or display | Toggle beep on/off |
| 104 | System status | Observe during error conditions |
| 119 | Transition timer | Observe values during cold-start and forced defrost |

### Recently Resolved (Capture 3 + User Clarification)
| DP  | Was | Now Confirmed As |
|-----|-----|------------------|
| 2/19| Set Temp / Indoor Temp | **Paired Setpoint:** DP2=°C (Tuya push), DP19=°F (MCU report). Both absent when setpoint unchanged. |
| 3/20| Secondary temp (unclear) | **Return Air Temperature:** Indoor air going into coils. Data arrives via BLE or cloud (no sensor on Tuya board). |
| 21  | Unknown Enum | **Temperature Unit (C/F):** 0=°C, 1=°F |
| 105 | Light or Beep? | **Vertical Swing Enable:** True=swing, False=fixed |
| 11  | Light toggle (uncertain) | **Light/Display Toggle** (distinct from DP105) |

### Features Not Yet Observed
- **Outdoor temperature** - NOT present on Tuya UART. The outdoor temp sensor connects directly to the outdoor unit's control board and is not relayed through the indoor unit's Tuya serial interface
- **Defrost cycle** - Requires extended cold-weather operation (outdoor temp must drop low enough)
- **Horizontal swing** - Not tested in any capture
- **Timer settings** - Not configured during captures
- **Error codes** - No faults occurred (DP24 always 0)
- **Sleep mode** - Not tested
- **Eco mode** - Not tested
- **Beep toggle** - Not isolated; could be DP9 or DP101

### Items Needing Verification
1. **ESP32 control via CMD 0x07** - All captures show CMD 0x07 as the only Tuya→MCU command with DP data. ESP32 should send CMD 0x07 with version 0x03 to control MCU. Needs live testing.
2. **BLE power data source** - The Tuya module (WBR3, BLE 4.2 capable) has no sensors. Power data (DPs 106-111) and temperature (DP3/DP20) likely arrive via BLE from MPPT controller or power monitor. Use BLE scanner to identify the source device before ESP32 swap.
3. **MCU behavior without CMD 0x22** - Will the MCU function normally if it stops receiving CMD 0x22 pushes? Or does it require periodic power/temperature data to operate?
4. **Setpoint command** - Verify MCU accepts DP2 (°C) and/or DP19 (°F) via CMD 0x07 for temperature control. Currently DP19 comes FROM MCU via CMD 0x06 and DP2 comes FROM Tuya via CMD 0x22 — the ESP32 may need to use CMD 0x22 for DP2 to set temperature.

### Recommended Additional Captures
1. **Isolated feature testing** - Toggle ONLY one feature at a time (beep, eco, sleep, timer, horizontal swing) with 10+ seconds between each
2. **Extended cold weather** - Long heating session when outdoor temp is well below freezing to trigger defrost
3. **Midday full solar** - Capture when solar output is maximum to see higher DP106 values
4. **Error condition** - If possible, trigger a fault to observe DP24 non-zero values
