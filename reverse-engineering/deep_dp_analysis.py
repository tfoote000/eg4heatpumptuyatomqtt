#!/usr/bin/env python3
"""
Deep DP Analysis - properly handles concatenated/burst Tuya packets.

The basic reassembler misses DPs when multiple 55 AA packets arrive in one
serial read burst. This script:
1. Finds ALL 55 AA packet boundaries in the raw byte stream
2. Decodes every sub-packet
3. Extracts every DP with proper timestamps
4. Correlates DPs with user actions (mode changes, fan, vane, light, etc.)
"""

import json
import sys
from collections import defaultdict

def load_capture(filename):
    with open(filename) as f:
        raw = f.read()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        last = raw.rfind('},')
        if last == -1:
            last = raw.rfind('}')
        return json.loads(raw[:last + 1] + "\n  ]\n}")


def build_byte_stream(entries):
    """Build ordered byte streams per source, preserving timestamps."""
    streams = {"tuya_to_mcu": [], "mcu_to_tuya": []}
    for e in entries:
        src = e["source"]
        ts = e["timestamp"]
        ts_ms = e.get("timestamp_ms", 0)
        for b in e["raw_bytes"]:
            streams[src].append({"byte": b, "ts": ts, "ts_ms": ts_ms})
    return streams


def find_packets_in_stream(byte_records):
    """Find all 55 AA framed packets in a byte stream."""
    packets = []
    i = 0
    n = len(byte_records)

    while i < n - 1:
        # Look for 55 AA
        if byte_records[i]["byte"] == 0x55 and byte_records[i + 1]["byte"] == 0xAA:
            ts = byte_records[i]["ts"]
            ts_ms = byte_records[i]["ts_ms"]
            # Need at least 7 bytes: 55 AA VER CMD LEN_H LEN_L ... CHK
            if i + 6 >= n:
                break
            ver = byte_records[i + 2]["byte"]
            cmd = byte_records[i + 3]["byte"]
            len_h = byte_records[i + 4]["byte"]
            len_l = byte_records[i + 5]["byte"]
            dlen = (len_h << 8) | len_l
            pkt_len = 6 + dlen + 1  # header(6) + data + checksum(1)

            if i + pkt_len > n:
                # Incomplete packet at end of stream
                break

            raw_bytes = [byte_records[i + j]["byte"] for j in range(pkt_len)]

            # Verify checksum
            calc_chk = sum(raw_bytes[:-1]) & 0xFF
            actual_chk = raw_bytes[-1]

            data = raw_bytes[6:6 + dlen]

            packets.append({
                "ts": ts,
                "ts_ms": ts_ms,
                "ver": ver,
                "cmd": cmd,
                "data": data,
                "raw": raw_bytes,
                "chk_ok": calc_chk == actual_chk,
            })
            i += pkt_len
        else:
            i += 1

    return packets


DP_TYPES = {0x01: "Boolean", 0x02: "Value", 0x03: "String", 0x04: "Enum", 0x05: "Fault"}


def parse_dps(data_bytes):
    """Parse all DPs from a data payload."""
    dps = []
    i = 0
    while i < len(data_bytes) - 3:
        dp_id = data_bytes[i]
        dp_type = data_bytes[i + 1]
        dp_len = (data_bytes[i + 2] << 8) | data_bytes[i + 3]
        if i + 4 + dp_len > len(data_bytes):
            break
        val_bytes = data_bytes[i + 4:i + 4 + dp_len]
        if dp_type == 0x01:  # Boolean
            val = bool(val_bytes[0]) if val_bytes else None
        elif dp_type == 0x02:  # Value (int32, could be signed)
            val_unsigned = int.from_bytes(val_bytes, 'big', signed=False)
            val_signed = int.from_bytes(val_bytes, 'big', signed=True)
            val = val_signed if val_signed < 0 else val_unsigned
        elif dp_type == 0x04:  # Enum
            val = val_bytes[0] if val_bytes else None
        elif dp_type == 0x05:  # Fault bitmap
            val = int.from_bytes(val_bytes, 'big', signed=False)
        elif dp_type == 0x03:  # String
            val = bytes(val_bytes).decode('ascii', errors='replace')
        else:
            val = val_bytes

        dps.append({
            "dp_id": dp_id,
            "dp_type": dp_type,
            "dp_type_name": DP_TYPES.get(dp_type, f"0x{dp_type:02X}"),
            "value": val,
            "raw_bytes": val_bytes,
        })
        i += 4 + dp_len
    return dps


def analyze(filename):
    data = load_capture(filename)
    entries = data["entries"]
    print(f"Loaded {len(entries)} entries from {filename}")

    streams = build_byte_stream(entries)

    all_dp_events = []  # Flat list of all DP events with timestamps

    for src_name, src_label, dir_label in [
        ("tuya_to_mcu", "TUY", "Tuya→MCU"),
        ("mcu_to_tuya", "MCU", "MCU→Tuya"),
    ]:
        stream = streams[src_name]
        packets = find_packets_in_stream(stream)
        print(f"\n{dir_label}: {len(packets)} packets found in byte stream")

        for pkt in packets:
            cmd = pkt["cmd"]
            if cmd in (0x06, 0x07, 0x22) and pkt["data"]:
                dps = parse_dps(pkt["data"])
                for dp in dps:
                    all_dp_events.append({
                        "ts": pkt["ts"],
                        "ts_ms": pkt["ts_ms"],
                        "dir": dir_label,
                        "cmd": cmd,
                        "dp_id": dp["dp_id"],
                        "dp_type": dp["dp_type"],
                        "dp_type_name": dp["dp_type_name"],
                        "value": dp["value"],
                        "raw_bytes": dp["raw_bytes"],
                    })

    # Sort by timestamp
    all_dp_events.sort(key=lambda e: e["ts_ms"])

    # =========================================================================
    # SECTION 1: Complete DP inventory
    # =========================================================================
    print("\n" + "=" * 100)
    print("COMPLETE DP INVENTORY (from deep byte-stream parsing)")
    print("=" * 100)

    dp_info = defaultdict(lambda: {
        "type_name": "",
        "dp_type": 0,
        "values": set(),
        "count": 0,
        "directions": set(),
        "commands": set(),
        "events": [],
    })

    for evt in all_dp_events:
        info = dp_info[evt["dp_id"]]
        info["type_name"] = evt["dp_type_name"]
        info["dp_type"] = evt["dp_type"]
        info["count"] += 1
        info["directions"].add(evt["dir"])
        info["commands"].add(evt["cmd"])
        if isinstance(evt["value"], (int, float, bool, str)):
            info["values"].add(evt["value"])
        info["events"].append(evt)

    print(f"\nTotal unique DPs: {len(dp_info)}")
    print(f"Total DP events: {len(all_dp_events)}")

    print(f"\n{'DP':<6} {'Type':<10} {'Direction(s)':<24} {'Cmds':<16} {'Count':<8} {'Values'}")
    print("-" * 120)

    for dp_id in sorted(dp_info.keys()):
        info = dp_info[dp_id]
        dirs = ", ".join(sorted(info["directions"]))
        cmds = ", ".join(f"0x{c:02X}" for c in sorted(info["commands"]))
        vals = sorted(info["values"])
        val_str = str(vals[:15])
        if len(vals) > 15:
            val_str += f" ... ({len(vals)} total)"
        print(f"DP {dp_id:<4} {info['type_name']:<10} {dirs:<24} {cmds:<16} {info['count']:<8} {val_str}")

    # =========================================================================
    # SECTION 2: Detailed per-DP analysis
    # =========================================================================
    print("\n" + "=" * 100)
    print("DETAILED PER-DP ANALYSIS")
    print("=" * 100)

    for dp_id in sorted(dp_info.keys()):
        info = dp_info[dp_id]
        print(f"\n--- DP {dp_id} ({info['type_name']}) ---")
        print(f"  Count: {info['count']}")
        print(f"  Directions: {', '.join(sorted(info['directions']))}")
        print(f"  Commands: {', '.join(f'0x{c:02X}' for c in sorted(info['commands']))}")
        vals = sorted(info["values"])
        print(f"  Unique values ({len(vals)}): {vals[:30]}")

        if info["dp_type"] == 0x02 and vals:  # Value type
            numeric = [v for v in vals if isinstance(v, (int, float))]
            if numeric:
                print(f"  Range: [{min(numeric)} - {max(numeric)}]")
                if min(numeric) < 0:
                    print(f"  ** SIGNED VALUES DETECTED **")

        # Show chronological events (limit to 40)
        print(f"  Timeline:")
        shown = 0
        prev_val = None
        for evt in info["events"]:
            ts_short = evt["ts"][-12:]
            cmd_name = {0x06: "MCU_RPT", 0x07: "TUY_ACK", 0x22: "TUY_PUSH"}
            cn = cmd_name.get(evt["cmd"], f"0x{evt['cmd']:02X}")
            val = evt["value"]
            marker = " <<<" if val != prev_val else ""
            if shown < 40 or marker:
                print(f"    [{ts_short}] {evt['dir']:<12} {cn:<10} = {val}{marker}")
                shown += 1
            prev_val = val
        if shown < info["count"]:
            print(f"    ... ({info['count'] - shown} more events)")

    # =========================================================================
    # SECTION 3: Init sequence analysis
    # =========================================================================
    print("\n" + "=" * 100)
    print("INIT SEQUENCE ANALYSIS (CMD 0x08 Query DP and full state dumps)")
    print("=" * 100)

    # Look for CMD 0x08 (query DP) and the response burst
    for src_name, dir_label in [("tuya_to_mcu", "Tuya→MCU"), ("mcu_to_tuya", "MCU→Tuya")]:
        stream = streams[src_name]
        packets = find_packets_in_stream(stream)
        for pkt in packets:
            if pkt["cmd"] == 0x08:
                print(f"\n  CMD 0x08 Query DP at {pkt['ts'][-12:]}")
                print(f"    Direction: {dir_label}")
                print(f"    Data: {' '.join(f'{b:02X}' for b in pkt['data'])}")
            elif pkt["cmd"] in (0x01, 0x02, 0x03):
                cmd_names = {0x01: "Product Info", 0x02: "MCU Config", 0x03: "Network Status"}
                print(f"\n  CMD 0x{pkt['cmd']:02X} {cmd_names.get(pkt['cmd'], '?')} at {pkt['ts'][-12:]}")
                print(f"    Direction: {dir_label}")
                if pkt["data"]:
                    data_hex = ' '.join(f'{b:02X}' for b in pkt["data"])
                    data_ascii = ''.join(chr(b) if 32 <= b < 127 else '.' for b in pkt["data"])
                    print(f"    Data: {data_hex}")
                    print(f"    ASCII: {data_ascii}")

    # =========================================================================
    # SECTION 4: Correlated event timeline
    # =========================================================================
    print("\n" + "=" * 100)
    print("CORRELATED EVENT TIMELINE (mode changes, toggles, temp changes)")
    print("=" * 100)

    # Filter to interesting DPs (not power monitoring)
    control_dps = {1, 2, 3, 4, 6, 9, 10, 11, 19, 20, 21, 22, 23, 24, 101, 104, 105, 112, 119}
    prev_values = {}

    print(f"\n{'Time':<14} {'Dir':<12} {'Cmd':<10} {'DP':<6} {'Type':<8} {'Value':<20} {'Note'}")
    print("-" * 100)

    for evt in all_dp_events:
        if evt["dp_id"] not in control_dps:
            continue
        dp_id = evt["dp_id"]
        val = evt["value"]

        # Determine if value changed
        changed = prev_values.get(dp_id) != val
        prev_values[dp_id] = val

        if not changed:
            continue  # Only show changes

        ts = evt["ts"][-12:]
        cmd_name = {0x06: "MCU_RPT", 0x07: "TUY_ACK", 0x22: "TUY_PUSH"}.get(evt["cmd"], f"0x{evt['cmd']:02X}")

        # Generate notes
        note = ""
        if dp_id == 1:
            note = "POWER " + ("ON" if val else "OFF")
        elif dp_id == 2:
            note = f"SET TEMP = {val}°C ({val * 9 / 5 + 32:.0f}°F)"
        elif dp_id == 3:
            if isinstance(val, int) and val < 100:
                note = f"TEMP READING = {val}°C ({val * 9 / 5 + 32:.0f}°F)"
            else:
                note = f"TEMP READING = {val} (raw)"
        elif dp_id == 4:
            mode_map = {0: "AUTO", 1: "COOL", 2: "HEAT", 3: "DRY", 4: "FAN_ONLY"}
            note = f"MODE = {mode_map.get(val, f'?({val})')}"
        elif dp_id == 22:
            mode_map = {0: "AUTO", 1: "COOL", 2: "HEAT", 3: "DRY", 4: "FAN_ONLY"}
            note = f"MODE (Tuya) = {mode_map.get(val, f'?({val})')}"
        elif dp_id == 19:
            note = f"INDOOR TEMP = {val}°F ({(val - 32) * 5 / 9:.1f}°C)"
        elif dp_id == 20:
            note = f"TEMP READING = {val}°F ({(val - 32) * 5 / 9:.1f}°C)"
        elif dp_id == 105:
            note = "TOGGLE → " + ("ON" if val else "OFF")
        elif dp_id == 112:
            note = "VANE/SWING → " + ("ON" if val else "OFF")
        elif dp_id == 11:
            note = "TOGGLE → " + ("ON" if val else "OFF")
        elif dp_id == 119:
            note = f"VALUE = {val}"

        print(f"{ts:<14} {evt['dir']:<12} {cmd_name:<10} DP{dp_id:<4} {evt['dp_type_name']:<8} {str(val):<20} {note}")

    # =========================================================================
    # SECTION 5: Temperature correlation analysis
    # =========================================================================
    print("\n" + "=" * 100)
    print("TEMPERATURE CORRELATION: DP2/DP19 vs DP3/DP20")
    print("=" * 100)

    # Get latest values at each timestamp
    temp_events = []
    latest = {}
    for evt in all_dp_events:
        if evt["dp_id"] in (2, 3, 19, 20):
            latest[evt["dp_id"]] = evt["value"]
            if all(k in latest for k in (2, 3, 19, 20)):
                temp_events.append({
                    "ts": evt["ts"][-12:],
                    "dp2": latest[2],
                    "dp3": latest[3],
                    "dp19": latest[19],
                    "dp20": latest[20],
                })

    if temp_events:
        print(f"\n{'Time':<14} {'DP2(°C)':<10} {'DP19(°F)':<10} {'DP2→°F':<10} {'Match?':<8} {'DP3(°C)':<10} {'DP20(°F)':<10} {'DP3→°F':<10} {'Match?'}")
        print("-" * 100)
        for te in temp_events[-30:]:
            dp2_f = te["dp2"] * 9 / 5 + 32
            dp3_f = te["dp3"] * 9 / 5 + 32 if isinstance(te["dp3"], int) and -50 < te["dp3"] < 150 else "N/A"
            m1 = "YES" if abs(dp2_f - te["dp19"]) <= 2 else "NO"
            m2 = "YES" if isinstance(dp3_f, float) and abs(dp3_f - te["dp20"]) <= 2 else "?"
            print(f"{te['ts']:<14} {te['dp2']:<10} {te['dp19']:<10} {dp2_f:<10.1f} {m1:<8} {te['dp3']:<10} {te['dp20']:<10} {str(dp3_f):<10} {m2}")

    # =========================================================================
    # SECTION 6: DP4 vs DP22 correlation (both should be mode)
    # =========================================================================
    print("\n" + "=" * 100)
    print("MODE CORRELATION: DP4 (MCU) vs DP22 (Tuya)")
    print("=" * 100)

    mode_map = {0: "AUTO", 1: "COOL", 2: "HEAT", 3: "DRY", 4: "FAN_ONLY"}
    latest_mode = {}
    for evt in all_dp_events:
        if evt["dp_id"] in (4, 22):
            latest_mode[evt["dp_id"]] = evt["value"]
            if all(k in latest_mode for k in (4, 22)):
                m4 = mode_map.get(latest_mode[4], f"?{latest_mode[4]}")
                m22 = mode_map.get(latest_mode[22], f"?{latest_mode[22]}")
                match = "MATCH" if latest_mode[4] == latest_mode[22] else "MISMATCH"
                print(f"  [{evt['ts'][-12:]}] DP4={latest_mode[4]}({m4}) DP22={latest_mode[22]}({m22}) {match}")

    # =========================================================================
    # SECTION 7: Power model verification (same as before but with deep parsing)
    # =========================================================================
    print("\n" + "=" * 100)
    print("POWER MODEL VERIFICATION")
    print("=" * 100)

    latest_power = {}
    power_checks = []
    for evt in all_dp_events:
        if evt["dp_id"] in (106, 108, 109, 111):
            latest_power[evt["dp_id"]] = evt["value"]
            if all(k in latest_power for k in (106, 108, 109, 111)):
                solar_w = latest_power[106]
                grid_w = latest_power[111]
                total = solar_w + grid_w
                if total > 0:
                    calc_s = round(solar_w / total * 100)
                    calc_g = round(grid_w / total * 100)
                    err_s = abs(calc_s - latest_power[108])
                    err_g = abs(calc_g - latest_power[109])
                    power_checks.append({"err_s": err_s, "err_g": err_g})

    if power_checks:
        avg_s = sum(c["err_s"] for c in power_checks) / len(power_checks)
        avg_g = sum(c["err_g"] for c in power_checks) / len(power_checks)
        max_s = max(c["err_s"] for c in power_checks)
        max_g = max(c["err_g"] for c in power_checks)
        print(f"  Samples: {len(power_checks)}")
        print(f"  Avg Solar% error: {avg_s:.2f}, Max: {max_s}")
        print(f"  Avg Grid% error: {avg_g:.2f}, Max: {max_g}")
        print(f"  Model CONFIRMED" if avg_s < 3 and avg_g < 3 else "  Model NEEDS REVIEW")

    # =========================================================================
    # SECTION 8: DP Identification Summary
    # =========================================================================
    print("\n" + "=" * 100)
    print("DP IDENTIFICATION SUMMARY")
    print("=" * 100)

    identifications = {
        1: ("Power ON/OFF", "HIGH", "Boolean toggle, ON=running, OFF=standby"),
        2: ("Set Temperature (°C)", "HIGH", "Tuya pushes target temp to MCU, matches DP19 range/2"),
        3: ("Temperature Sensor (°C)", "MEDIUM", "Paired with DP20 in °F. Likely pipe/coil or outdoor temp"),
        4: ("HVAC Mode (MCU)", "HIGH", "0=Auto,1=Cool,2=Heat,3=Dry,4=Fan. MCU reports via CMD 0x06"),
        6: ("Unknown Boolean", "LOW", "Always OFF. Appears during mode changes. Possible: quiet/eco/aux"),
        9: ("Unknown Boolean", "LOW", "Only in init dump, always OFF. Possible: eco/quiet/sleep"),
        10: ("Compressor/Heating Active", "MEDIUM", "ON during active heating, OFF when stopped"),
        11: ("Light/Display Toggle", "MEDIUM", "Toggled ON/OFF by user. Could be display or light"),
        19: ("Indoor Temperature (°F)", "HIGH", "MCU measures and reports. Range 68-75°F = 20-24°C"),
        20: ("Temperature Sensor (°F)", "MEDIUM", "Paired with DP3 in °C. Possibly pipe/coil or outdoor"),
        21: ("Unknown Enum", "LOW", "Only value 1 seen. Possibly fan direction or vane position"),
        22: ("HVAC Mode (Tuya)", "HIGH", "Same mapping as DP4. Tuya pushes to MCU via CMD 0x22"),
        23: ("Fan Speed or Swing", "MEDIUM", "Values 0-1. Changes during mode transitions"),
        24: ("Fault Bitmap", "HIGH", "Type 0x05 (Fault). Value 0 = no fault"),
        101: ("Unknown Boolean", "LOW", "Only in init dump, always OFF. Possible: beep/light/eco"),
        104: ("Unknown Boolean", "MEDIUM", "Always ON when seen. Possibly 'compressor enabled'"),
        105: ("Toggle (Light/Beep?)", "MEDIUM", "User toggled ON then OFF. Could be display light"),
        106: ("Solar Power (W)", "CONFIRMED", "Verified mathematically with DP108"),
        107: ("Solar Energy Counter (Wh)", "CONFIRMED", "Monotonic counter, rate matches solar power"),
        108: ("Solar Power %", "CONFIRMED", "= round(DP106 / (DP106+DP111) * 100)"),
        109: ("Grid Power %", "CONFIRMED", "= round(DP111 / (DP106+DP111) * 100), DP108+DP109≈100"),
        110: ("Total Energy Counter (Wh)", "CONFIRMED", "Monotonic counter, rate matches total power"),
        111: ("Grid Power (W)", "CONFIRMED", "Verified mathematically with DP109"),
        112: ("Vertical Vane/Swing", "MEDIUM", "MCU sends OFF repeatedly, Tuya acks ON. Stepping pattern"),
        119: ("Timer/Delay Value", "LOW", "Values 0,10,40. 10↔0 during mode changes, 40 during off"),
    }

    print(f"\n{'DP':<6} {'Confidence':<12} {'Identification':<35} {'Notes'}")
    print("-" * 120)
    for dp_id in sorted(identifications.keys()):
        name, conf, notes = identifications[dp_id]
        print(f"DP {dp_id:<4} {conf:<12} {name:<35} {notes}")

    # Check if any DPs were found but not identified
    for dp_id in sorted(dp_info.keys()):
        if dp_id not in identifications:
            print(f"DP {dp_id:<4} {'???':<12} {'UNIDENTIFIED':<35} Values: {sorted(dp_info[dp_id]['values'])[:10]}")


if __name__ == "__main__":
    fn = sys.argv[1] if len(sys.argv) > 1 else "uart_capture_20260218_184142.json"
    analyze(fn)
