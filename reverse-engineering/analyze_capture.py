#!/usr/bin/env python3
"""
Comprehensive UART Capture Analyzer for EG4 Hybrid Solar Mini-Split Heat Pump
Analyzes Tuya protocol packets to map all Data Points (DPs)
"""

import json
import sys
from collections import defaultdict, Counter
from datetime import datetime

# DP Type names
DP_TYPE_NAMES = {
    0x01: "Boolean",
    0x02: "Value (int32)",
    0x03: "String",
    0x04: "Enum",
    0x05: "Fault/Bitmap",
}

# Command names
CMD_NAMES = {
    0x00: "Heartbeat",
    0x01: "Query Product Info",
    0x02: "Query MCU Config",
    0x03: "Report Network Status",
    0x04: "Reset WiFi (Smart Config)",
    0x05: "Reset WiFi (AP Mode)",
    0x06: "Report DP Status (MCU→Tuya)",
    0x07: "DP Status Ack (Tuya→MCU)",
    0x08: "Query DP Status",
    0x09: "OTA Upgrade",
    0x0A: "Get Local Time",
    0x22: "Send DP (Tuya→MCU)",
    0x23: "Send DP Ack (MCU→Tuya)",
}


def reassemble_packets(entries):
    """
    Reassemble Tuya packets from raw_stream entries.
    The capture often splits 0x55 into its own entry, with the rest following.
    We need to reconstruct full 55 AA ... packets.
    """
    # Group consecutive entries by source
    packets = []
    pending = {}  # source -> accumulated bytes + first timestamp

    for entry in entries:
        source = entry["source"]
        raw_bytes = entry["raw_bytes"]
        timestamp = entry["timestamp"]
        ts_ms = entry.get("timestamp_ms", 0)

        if source not in pending:
            pending[source] = {"bytes": [], "timestamp": timestamp, "ts_ms": ts_ms}

        buf = pending[source]

        # If we get a 0x55 and buffer is non-empty, flush what we have
        if raw_bytes == [0x55] and buf["bytes"]:
            # Flush current buffer
            if len(buf["bytes"]) >= 5:  # Minimum useful Tuya packet (after 55)
                packets.append({
                    "source": source,
                    "timestamp": buf["timestamp"],
                    "ts_ms": buf["ts_ms"],
                    "raw_bytes": [0x55] + buf["bytes"],
                })
            buf["bytes"] = []
            buf["timestamp"] = timestamp
            buf["ts_ms"] = ts_ms
        elif raw_bytes == [0x55] and not buf["bytes"]:
            # First 0x55, just note the timestamp
            buf["timestamp"] = timestamp
            buf["ts_ms"] = ts_ms
        else:
            # Continuation bytes
            if not buf["bytes"] and raw_bytes and raw_bytes[0] == 0xAA:
                # This follows a 0x55 - good, this is a Tuya packet body
                buf["bytes"] = raw_bytes
            elif buf["bytes"]:
                # More continuation
                buf["bytes"].extend(raw_bytes)
            else:
                # No pending 0x55, this is standalone data
                # Check if it starts with 55 AA
                if len(raw_bytes) >= 2 and raw_bytes[0] == 0x55 and raw_bytes[1] == 0xAA:
                    buf["bytes"] = raw_bytes[1:]  # Store from AA onward
                    buf["timestamp"] = timestamp
                    buf["ts_ms"] = ts_ms
                else:
                    # Unknown data, skip
                    pass

    # Flush remaining
    for source, buf in pending.items():
        if len(buf["bytes"]) >= 5:
            packets.append({
                "source": source,
                "timestamp": buf["timestamp"],
                "ts_ms": buf["ts_ms"],
                "raw_bytes": [0x55] + buf["bytes"],
            })

    return sorted(packets, key=lambda p: p["timestamp"])


def decode_tuya_packet(raw_bytes):
    """Decode a Tuya protocol packet from raw bytes."""
    if len(raw_bytes) < 7:
        return None
    if raw_bytes[0] != 0x55 or raw_bytes[1] != 0xAA:
        return None

    version = raw_bytes[2]
    command = raw_bytes[3]
    data_length = (raw_bytes[4] << 8) | raw_bytes[5]
    data = raw_bytes[6:6 + data_length]

    if len(raw_bytes) < 7 + data_length:
        return {"valid": False, "reason": "truncated", "version": version, "command": command}

    checksum_byte = raw_bytes[6 + data_length]

    # Verify checksum: sum of all bytes from header (55) to end of data, mod 256
    calc_checksum = sum(raw_bytes[:6 + data_length]) % 256
    checksum_valid = calc_checksum == checksum_byte

    return {
        "valid": checksum_valid,
        "version": version,
        "command": command,
        "command_name": CMD_NAMES.get(command, f"Unknown(0x{command:02X})"),
        "data_length": data_length,
        "data": data,
        "data_hex": " ".join(f"{b:02X}" for b in data),
        "checksum": checksum_byte,
        "checksum_valid": checksum_valid,
        "calc_checksum": calc_checksum,
        "raw_hex": " ".join(f"{b:02X}" for b in raw_bytes),
    }


def parse_data_points(data_bytes):
    """Parse Tuya Data Points from a data payload."""
    dps = []
    i = 0
    while i < len(data_bytes) - 3:
        dp_id = data_bytes[i]
        dp_type = data_bytes[i + 1]
        dp_len = (data_bytes[i + 2] << 8) | data_bytes[i + 3]

        if i + 4 + dp_len > len(data_bytes):
            break

        dp_value_bytes = data_bytes[i + 4:i + 4 + dp_len]

        # Decode based on type
        if dp_type == 0x01:  # Boolean
            value = bool(dp_value_bytes[0]) if dp_value_bytes else None
            value_str = "ON" if value else "OFF"
        elif dp_type == 0x02:  # Value (32-bit big-endian)
            value = int.from_bytes(dp_value_bytes, 'big', signed=False) if dp_value_bytes else None
            # Also try signed
            value_signed = int.from_bytes(dp_value_bytes, 'big', signed=True) if dp_value_bytes else None
            value_str = f"{value} (signed: {value_signed})" if value != value_signed else str(value)
        elif dp_type == 0x03:  # String
            try:
                value = bytes(dp_value_bytes).decode('ascii', errors='replace')
            except:
                value = dp_value_bytes
            value_str = str(value)
        elif dp_type == 0x04:  # Enum
            value = int.from_bytes(dp_value_bytes, 'big') if dp_value_bytes else None
            value_str = str(value)
        elif dp_type == 0x05:  # Fault/Bitmap
            value = int.from_bytes(dp_value_bytes, 'big') if dp_value_bytes else None
            if value is not None:
                value_str = f"0x{value:0{dp_len*2}X} (bits: {bin(value)})"
            else:
                value_str = "None"
        else:
            value = dp_value_bytes
            value_str = " ".join(f"{b:02X}" for b in dp_value_bytes)

        dps.append({
            "id": dp_id,
            "type": dp_type,
            "type_name": DP_TYPE_NAMES.get(dp_type, f"Unknown(0x{dp_type:02X})"),
            "length": dp_len,
            "value": value,
            "value_str": value_str,
            "raw_bytes": dp_value_bytes,
        })

        i += 4 + dp_len

    return dps


def analyze_capture(filename):
    """Main analysis function."""
    with open(filename) as f:
        raw = f.read()

    # Handle truncated JSON files
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"WARNING: JSON is truncated at position {e.pos}, attempting recovery...")
        # Find the last complete entry by looking for the last "},"
        last_complete = raw.rfind('},')
        if last_complete == -1:
            last_complete = raw.rfind('}')
        if last_complete > 0:
            # Close the JSON properly
            fixed = raw[:last_complete + 1] + "\n  ]\n}"
            data = json.loads(fixed)
            print(f"  Recovered {len(data.get('entries', []))} entries")

    print("=" * 80)
    print("EG4 HYBRID SOLAR MINI-SPLIT UART CAPTURE ANALYSIS")
    print("=" * 80)
    print(f"\nCapture file: {filename}")
    print(f"Start time: {data['capture_info']['start_time']}")
    print(f"Baud rate: {data['capture_info']['baud_rate']}")
    print(f"Tuya port: {data['capture_info']['tuya_port']}")
    print(f"MCU port: {data['capture_info']['mcu_port']}")
    print(f"Total raw entries: {len(data['entries'])}")

    # Reassemble packets
    print("\n--- Reassembling Packets ---")
    packets = reassemble_packets(data["entries"])
    print(f"Reassembled packets: {len(packets)}")

    # Decode all packets
    decoded_packets = []
    decode_errors = 0
    for pkt in packets:
        decoded = decode_tuya_packet(pkt["raw_bytes"])
        if decoded and decoded.get("valid", False):
            decoded_packets.append({
                "source": pkt["source"],
                "timestamp": pkt["timestamp"],
                "ts_ms": pkt["ts_ms"],
                "decoded": decoded,
            })
        elif decoded:
            decode_errors += 1

    print(f"Successfully decoded: {len(decoded_packets)}")
    print(f"Decode errors: {decode_errors}")

    # === SECTION 1: Packet Statistics ===
    print("\n" + "=" * 80)
    print("SECTION 1: PACKET STATISTICS")
    print("=" * 80)

    cmd_counter = Counter()
    source_counter = Counter()
    direction_cmd = defaultdict(Counter)

    for pkt in decoded_packets:
        cmd = pkt["decoded"]["command"]
        cmd_name = pkt["decoded"]["command_name"]
        source = pkt["source"]
        direction = "MCU→Tuya" if source == "mcu_to_tuya" else "Tuya→MCU"

        cmd_counter[f"0x{cmd:02X} {cmd_name}"] += 1
        source_counter[direction] += 1
        direction_cmd[direction][f"0x{cmd:02X} {cmd_name}"] += 1

    print("\nPackets by command type:")
    for cmd, count in sorted(cmd_counter.items()):
        print(f"  {cmd}: {count}")

    print("\nPackets by direction:")
    for direction, count in sorted(source_counter.items()):
        print(f"  {direction}: {count}")

    print("\nPackets by direction + command:")
    for direction in sorted(direction_cmd.keys()):
        print(f"\n  {direction}:")
        for cmd, count in sorted(direction_cmd[direction].items()):
            print(f"    {cmd}: {count}")

    # === SECTION 2: Heartbeat Analysis ===
    print("\n" + "=" * 80)
    print("SECTION 2: HEARTBEAT ANALYSIS")
    print("=" * 80)

    heartbeats = [p for p in decoded_packets if p["decoded"]["command"] == 0x00]
    if heartbeats:
        hb_times = []
        for i in range(1, len(heartbeats)):
            if heartbeats[i]["source"] == heartbeats[i-1]["source"]:
                dt = heartbeats[i]["ts_ms"] - heartbeats[i-1]["ts_ms"]
                if dt > 0:
                    hb_times.append(dt)

        if hb_times:
            avg_interval = sum(hb_times) / len(hb_times)
            print(f"Heartbeat count: {len(heartbeats)}")
            print(f"Average interval: {avg_interval/1000:.1f}s")
            print(f"Min interval: {min(hb_times)/1000:.1f}s")
            print(f"Max interval: {max(hb_times)/1000:.1f}s")

        # Show heartbeat data
        print("\nHeartbeat examples:")
        for hb in heartbeats[:5]:
            d = hb["decoded"]
            print(f"  [{hb['timestamp'][-12:]}] [{hb['source'][:3].upper()}] "
                  f"v{d['version']} data: {d['data_hex'] or '(empty)'}")

    # === SECTION 3: ALL Data Points Found ===
    print("\n" + "=" * 80)
    print("SECTION 3: ALL DATA POINTS DISCOVERED")
    print("=" * 80)

    # Commands that carry DP data: 0x06, 0x07, 0x08, 0x22, 0x23
    dp_commands = {0x06, 0x07, 0x22, 0x23, 0x08}

    dp_registry = defaultdict(lambda: {
        "type": None,
        "type_name": None,
        "values_seen": [],
        "sources": set(),
        "commands": set(),
        "timestamps": [],
        "occurrences": 0,
    })

    all_dp_events = []  # Chronological list of all DP changes

    for pkt in decoded_packets:
        cmd = pkt["decoded"]["command"]
        if cmd not in dp_commands:
            continue

        data = pkt["decoded"]["data"]
        if not data:
            continue

        # For cmd 0x23, the data is typically just an ack byte (0x01), not DP data
        if cmd == 0x23:
            continue

        dps = parse_data_points(data)
        direction = "MCU→Tuya" if pkt["source"] == "mcu_to_tuya" else "Tuya→MCU"

        for dp in dps:
            dp_id = dp["id"]
            reg = dp_registry[dp_id]
            reg["type"] = dp["type"]
            reg["type_name"] = dp["type_name"]
            reg["values_seen"].append(dp["value"])
            reg["sources"].add(direction)
            reg["commands"].add(f"0x{cmd:02X}")
            reg["timestamps"].append(pkt["timestamp"])
            reg["occurrences"] += 1

            all_dp_events.append({
                "timestamp": pkt["timestamp"],
                "ts_ms": pkt["ts_ms"],
                "direction": direction,
                "command": cmd,
                "dp_id": dp_id,
                "dp_type": dp["type"],
                "dp_type_name": dp["type_name"],
                "value": dp["value"],
                "value_str": dp["value_str"],
                "raw_bytes": dp["raw_bytes"],
            })

    print(f"\nTotal unique Data Points discovered: {len(dp_registry)}")
    print(f"Total DP events: {len(all_dp_events)}")

    print("\n{:<6} {:<15} {:<15} {:<12} {:<8} {:<40} {}".format(
        "DP ID", "Type", "Direction(s)", "Command(s)", "Count", "Values Seen", "Value Range"))
    print("-" * 140)

    for dp_id in sorted(dp_registry.keys()):
        reg = dp_registry[dp_id]
        values = reg["values_seen"]
        unique_values = sorted(set(str(v) for v in values))

        if len(unique_values) > 8:
            val_str = f"{', '.join(unique_values[:4])} ... {', '.join(unique_values[-2:])}"
        else:
            val_str = ", ".join(unique_values)

        # Compute range for numeric types
        range_str = ""
        if reg["type"] in (0x02, 0x04):
            numeric_vals = [v for v in values if isinstance(v, (int, float))]
            if numeric_vals:
                range_str = f"[{min(numeric_vals)} - {max(numeric_vals)}]"

        directions = ", ".join(sorted(reg["sources"]))
        commands = ", ".join(sorted(reg["commands"]))

        print(f"DP {dp_id:<3}  {reg['type_name']:<15} {directions:<15} {commands:<12} {reg['occurrences']:<8} {val_str:<40} {range_str}")

    # === SECTION 4: Detailed DP Analysis ===
    print("\n" + "=" * 80)
    print("SECTION 4: DETAILED DATA POINT ANALYSIS")
    print("=" * 80)

    for dp_id in sorted(dp_registry.keys()):
        reg = dp_registry[dp_id]
        values = reg["values_seen"]

        print(f"\n--- DP {dp_id} ({reg['type_name']}) ---")
        print(f"  Occurrences: {reg['occurrences']}")
        print(f"  Direction(s): {', '.join(sorted(reg['sources']))}")
        print(f"  Command(s): {', '.join(sorted(reg['commands']))}")

        unique_values = sorted(set(values))
        print(f"  Unique values ({len(unique_values)}): ", end="")
        if len(unique_values) <= 20:
            print(unique_values)
        else:
            print(f"{unique_values[:10]} ... ({len(unique_values)} total)")

        if reg["type"] == 0x02:  # Value type - show stats
            numeric_vals = [v for v in values if isinstance(v, (int, float))]
            if numeric_vals:
                print(f"  Min: {min(numeric_vals)}, Max: {max(numeric_vals)}, "
                      f"Avg: {sum(numeric_vals)/len(numeric_vals):.1f}")
                # Check if values might be scaled temperatures
                for scale_name, divisor in [("raw", 1), ("÷2", 2), ("÷10", 10), ("÷100", 100)]:
                    scaled_min = min(numeric_vals) / divisor
                    scaled_max = max(numeric_vals) / divisor
                    if -40 <= scaled_min <= 150 and -40 <= scaled_max <= 150:
                        print(f"    If {scale_name}: {scaled_min:.1f} - {scaled_max:.1f} "
                              f"(plausible temperature range)")
                    if 0 <= scaled_min <= 5000 and 0 <= scaled_max <= 5000:
                        if divisor > 1:
                            print(f"    If {scale_name}: {scaled_min:.1f} - {scaled_max:.1f} "
                                  f"(plausible power/watts range)")

        # Show chronological value changes for this DP
        events = [e for e in all_dp_events if e["dp_id"] == dp_id]
        if len(events) <= 30:
            print(f"  Chronological values:")
            for evt in events:
                print(f"    [{evt['timestamp'][-12:]}] [{evt['direction']}] "
                      f"cmd=0x{evt['command']:02X} value={evt['value_str']}")
        else:
            # Show transitions (when value changes)
            print(f"  Value transitions (showing changes only):")
            prev_val = None
            shown = 0
            for evt in events:
                if evt["value"] != prev_val:
                    print(f"    [{evt['timestamp'][-12:]}] [{evt['direction']}] "
                          f"cmd=0x{evt['command']:02X} value={evt['value_str']}")
                    prev_val = evt["value"]
                    shown += 1
                    if shown > 30:
                        print(f"    ... ({len(events) - shown} more events)")
                        break

    # === SECTION 5: Command 0x06 Analysis (MCU DP Reports) ===
    print("\n" + "=" * 80)
    print("SECTION 5: MCU DP REPORTS (Command 0x06)")
    print("=" * 80)

    cmd06_packets = [p for p in decoded_packets if p["decoded"]["command"] == 0x06]
    print(f"\nTotal CMD 0x06 packets: {len(cmd06_packets)}")

    for pkt in cmd06_packets:
        d = pkt["decoded"]
        dps = parse_data_points(d["data"]) if d["data"] else []
        dp_str = " | ".join(f"DP{dp['id']}({dp['type_name']})={dp['value_str']}" for dp in dps)
        print(f"  [{pkt['timestamp'][-12:]}] [{pkt['source'][:3].upper()}] {d['raw_hex']}")
        print(f"    DPs: {dp_str}")

    # === SECTION 6: Command 0x07 Analysis (Tuya DP Acks) ===
    print("\n" + "=" * 80)
    print("SECTION 6: TUYA DP ACKS (Command 0x07)")
    print("=" * 80)

    cmd07_packets = [p for p in decoded_packets if p["decoded"]["command"] == 0x07]
    print(f"\nTotal CMD 0x07 packets: {len(cmd07_packets)}")

    for pkt in cmd07_packets:
        d = pkt["decoded"]
        dps = parse_data_points(d["data"]) if d["data"] else []
        dp_str = " | ".join(f"DP{dp['id']}({dp['type_name']})={dp['value_str']}" for dp in dps)
        print(f"  [{pkt['timestamp'][-12:]}] [{pkt['source'][:3].upper()}] {d['raw_hex']}")
        print(f"    DPs: {dp_str}")

    # === SECTION 7: Command 0x22 Analysis (Tuya→MCU DP Sends) ===
    print("\n" + "=" * 80)
    print("SECTION 7: TUYA→MCU DP SENDS (Command 0x22)")
    print("=" * 80)

    cmd22_packets = [p for p in decoded_packets if p["decoded"]["command"] == 0x22]
    print(f"\nTotal CMD 0x22 packets: {len(cmd22_packets)}")

    for pkt in cmd22_packets:
        d = pkt["decoded"]
        dps = parse_data_points(d["data"]) if d["data"] else []
        dp_str = " | ".join(f"DP{dp['id']}({dp['type_name']})={dp['value_str']}" for dp in dps)
        print(f"  [{pkt['timestamp'][-12:]}] [{pkt['source'][:3].upper()}] {d['raw_hex']}")
        print(f"    DPs: {dp_str}")

    # === SECTION 8: Command 0x23 Analysis (MCU Acks for 0x22) ===
    print("\n" + "=" * 80)
    print("SECTION 8: MCU ACKS FOR 0x22 (Command 0x23)")
    print("=" * 80)

    cmd23_packets = [p for p in decoded_packets if p["decoded"]["command"] == 0x23]
    print(f"\nTotal CMD 0x23 packets: {len(cmd23_packets)}")

    for pkt in cmd23_packets[:10]:
        d = pkt["decoded"]
        print(f"  [{pkt['timestamp'][-12:]}] [{pkt['source'][:3].upper()}] {d['raw_hex']}")
        print(f"    Data: {d['data_hex']}")

    if len(cmd23_packets) > 10:
        print(f"  ... ({len(cmd23_packets) - 10} more)")

    # === SECTION 9: Non-DP Commands ===
    print("\n" + "=" * 80)
    print("SECTION 9: NON-DP COMMANDS (Product Info, Network Status, etc.)")
    print("=" * 80)

    non_dp_cmds = {0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x08, 0x09, 0x0A}
    for cmd in sorted(non_dp_cmds):
        pkts = [p for p in decoded_packets if p["decoded"]["command"] == cmd]
        if pkts:
            print(f"\n--- Command 0x{cmd:02X} ({CMD_NAMES.get(cmd, 'Unknown')}) ---")
            print(f"  Count: {len(pkts)}")
            for pkt in pkts[:5]:
                d = pkt["decoded"]
                direction = "MCU→Tuya" if pkt["source"] == "mcu_to_tuya" else "Tuya→MCU"
                print(f"  [{pkt['timestamp'][-12:]}] [{direction}] v{d['version']} "
                      f"data({d['data_length']}): {d['data_hex']}")
            if len(pkts) > 5:
                print(f"  ... ({len(pkts) - 5} more)")

    # === SECTION 10: Timeline of All Events ===
    print("\n" + "=" * 80)
    print("SECTION 10: FULL PACKET TIMELINE (non-heartbeat)")
    print("=" * 80)

    non_hb = [p for p in decoded_packets if p["decoded"]["command"] != 0x00]
    print(f"\nTotal non-heartbeat packets: {len(non_hb)}")

    for pkt in non_hb:
        d = pkt["decoded"]
        direction = "MCU→Tuya" if pkt["source"] == "mcu_to_tuya" else "Tuya→MCU"

        # Parse DPs if applicable
        dp_str = ""
        if d["command"] in (0x06, 0x07, 0x22) and d["data"]:
            dps = parse_data_points(d["data"])
            dp_str = " | ".join(f"DP{dp['id']}={dp['value_str']}" for dp in dps)

        print(f"  [{pkt['timestamp'][-12:]}] [{direction:<10}] "
              f"cmd=0x{d['command']:02X}({d['command_name']:<30}) "
              f"data: {d['data_hex']:<50} {dp_str}")

    # === SECTION 11: Temporal Patterns ===
    print("\n" + "=" * 80)
    print("SECTION 11: TEMPORAL PATTERNS & UPDATE FREQUENCIES")
    print("=" * 80)

    for dp_id in sorted(dp_registry.keys()):
        events = [e for e in all_dp_events if e["dp_id"] == dp_id]
        if len(events) >= 2:
            intervals = []
            for i in range(1, len(events)):
                dt = events[i]["ts_ms"] - events[i-1]["ts_ms"]
                if dt > 0:
                    intervals.append(dt)
            if intervals:
                avg_interval = sum(intervals) / len(intervals)
                print(f"  DP {dp_id}: avg interval {avg_interval/1000:.1f}s, "
                      f"events: {len(events)}, "
                      f"min gap: {min(intervals)/1000:.1f}s, "
                      f"max gap: {max(intervals)/1000:.1f}s")

    # === SECTION 12: Protocol Pattern Summary ===
    print("\n" + "=" * 80)
    print("SECTION 12: PROTOCOL COMMUNICATION PATTERNS")
    print("=" * 80)

    # Look for request/response pairs
    print("\nRequest/Response Pairs observed:")
    for i in range(len(decoded_packets) - 1):
        curr = decoded_packets[i]
        next_pkt = decoded_packets[i + 1]

        # MCU cmd 0x06 followed by Tuya cmd 0x07
        if (curr["decoded"]["command"] == 0x06 and
            next_pkt["decoded"]["command"] == 0x07 and
            curr["source"] == "mcu_to_tuya" and
            next_pkt["source"] == "tuya_to_mcu"):

            curr_dps = parse_data_points(curr["decoded"]["data"]) if curr["decoded"]["data"] else []
            next_dps = parse_data_points(next_pkt["decoded"]["data"]) if next_pkt["decoded"]["data"] else []

            curr_dp_str = ", ".join(f"DP{dp['id']}={dp['value']}" for dp in curr_dps)
            next_dp_str = ", ".join(f"DP{dp['id']}={dp['value']}" for dp in next_dps)

            # Only print if values match (confirming it's an ack)
            if curr_dp_str == next_dp_str:
                time_delta = next_pkt["ts_ms"] - curr["ts_ms"]
                print(f"  MCU 0x06 [{curr_dp_str}] → Tuya 0x07 ack ({time_delta:.0f}ms)")

        # Tuya cmd 0x22 followed by MCU cmd 0x23
        if (curr["decoded"]["command"] == 0x22 and
            next_pkt["decoded"]["command"] == 0x23 and
            curr["source"] == "tuya_to_mcu" and
            next_pkt["source"] == "mcu_to_tuya"):

            curr_dps = parse_data_points(curr["decoded"]["data"]) if curr["decoded"]["data"] else []
            curr_dp_str = ", ".join(f"DP{dp['id']}={dp['value']}" for dp in curr_dps)
            time_delta = next_pkt["ts_ms"] - curr["ts_ms"]
            print(f"  Tuya 0x22 [{curr_dp_str}] → MCU 0x23 ack ({time_delta:.0f}ms)")

    # === SECTION 13: DP Candidate Identification ===
    print("\n" + "=" * 80)
    print("SECTION 13: DP IDENTIFICATION HYPOTHESES")
    print("=" * 80)

    for dp_id in sorted(dp_registry.keys()):
        reg = dp_registry[dp_id]
        values = reg["values_seen"]
        unique_values = sorted(set(values))

        hypotheses = []

        if reg["type"] == 0x01:  # Boolean
            if dp_id == 1:
                hypotheses.append("POWER STATE (ON/OFF)")
            else:
                hypotheses.append("Boolean control/status flag")

        elif reg["type"] == 0x02:  # Value
            numeric_vals = [v for v in values if isinstance(v, (int, float))]
            if numeric_vals:
                vmin, vmax = min(numeric_vals), max(numeric_vals)

                # Temperature candidates
                if 15 <= vmin <= 35 and 15 <= vmax <= 35:
                    hypotheses.append(f"Temperature in °C (raw: {vmin}-{vmax})")
                if 55 <= vmin <= 95 and 55 <= vmax <= 95:
                    hypotheses.append(f"Temperature in °F (raw: {vmin}-{vmax})")
                if 150 <= vmin <= 350 and 150 <= vmax <= 350:
                    hypotheses.append(f"Temperature ÷10 in °C ({vmin/10:.1f}-{vmax/10:.1f}°C)")

                # Power candidates
                if 0 <= vmin and vmax <= 5000:
                    hypotheses.append(f"Power in watts (raw: {vmin}-{vmax})")
                if vmax > 10000 and vmax < 10000000:
                    hypotheses.append(f"Energy in Wh or counter (raw: {vmin}-{vmax})")

                # Frequency
                if 0 <= vmin and vmax <= 120:
                    hypotheses.append(f"Compressor Hz or fan speed (raw: {vmin}-{vmax})")

        elif reg["type"] == 0x04:  # Enum
            if len(unique_values) >= 3 and len(unique_values) <= 6:
                hypotheses.append(f"Mode selector ({len(unique_values)} options: {unique_values})")
            elif len(unique_values) == 2:
                hypotheses.append(f"Binary selector ({unique_values})")

        elif reg["type"] == 0x05:  # Fault
            hypotheses.append("Error/fault bitmap")

        print(f"\n  DP {dp_id} ({reg['type_name']}):")
        print(f"    Values seen: {unique_values[:20]}")
        for h in hypotheses:
            print(f"    → Hypothesis: {h}")

    # === Summary ===
    print("\n" + "=" * 80)
    print("ANALYSIS COMPLETE")
    print("=" * 80)

    # Capture time range
    if decoded_packets:
        first_ts = decoded_packets[0]["timestamp"]
        last_ts = decoded_packets[-1]["timestamp"]
        duration_ms = decoded_packets[-1]["ts_ms"] - decoded_packets[0]["ts_ms"]
        print(f"\nCapture window: {first_ts} → {last_ts}")
        print(f"Duration: {duration_ms/1000:.1f}s ({duration_ms/60000:.1f} min)")

    print(f"\nTotal decoded packets: {len(decoded_packets)}")
    print(f"Unique DPs found: {len(dp_registry)}")
    print(f"DP IDs: {sorted(dp_registry.keys())}")


if __name__ == "__main__":
    filename = sys.argv[1] if len(sys.argv) > 1 else "uart_capture_20260218_182150.json"
    analyze_capture(filename)
