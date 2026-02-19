#!/usr/bin/env python3
"""
Verify the hypothesis: DP106=Solar(W), DP111=Grid(W), DP108=Solar%, DP109=Grid%
Where: Total = DP106 + DP111, DP108 = DP106/Total*100, DP109 = DP111/Total*100
"""

import json

with open("uart_capture_20260218_182150.json") as f:
    raw = f.read()
try:
    data = json.loads(raw)
except json.JSONDecodeError:
    last = raw.rfind('},')
    data = json.loads(raw[:last+1] + "\n  ]\n}")

# Reassemble and parse (simplified)
def reassemble(entries):
    packets = []
    pending = {}
    for e in entries:
        src = e["source"]
        rb = e["raw_bytes"]
        ts = e["timestamp"]
        ts_ms = e.get("timestamp_ms", 0)
        if src not in pending:
            pending[src] = {"bytes": [], "ts": ts, "ts_ms": ts_ms}
        buf = pending[src]
        if rb == [0x55] and buf["bytes"]:
            if len(buf["bytes"]) >= 5:
                packets.append({"src": src, "ts": buf["ts"], "ts_ms": buf["ts_ms"], "raw": [0x55]+buf["bytes"]})
            buf["bytes"] = []
            buf["ts"] = ts
            buf["ts_ms"] = ts_ms
        elif rb == [0x55]:
            buf["ts"] = ts
            buf["ts_ms"] = ts_ms
        elif not buf["bytes"] and rb and rb[0] == 0xAA:
            buf["bytes"] = rb
        elif buf["bytes"]:
            buf["bytes"].extend(rb)
    return sorted(packets, key=lambda p: p["ts"])

def parse_dp(data_bytes):
    dps = {}
    i = 0
    while i < len(data_bytes) - 3:
        dp_id = data_bytes[i]
        dp_type = data_bytes[i+1]
        dp_len = (data_bytes[i+2] << 8) | data_bytes[i+3]
        if i + 4 + dp_len > len(data_bytes): break
        val_bytes = data_bytes[i+4:i+4+dp_len]
        if dp_type == 0x01:
            val = bool(val_bytes[0])
        elif dp_type in (0x02, 0x04):
            val = int.from_bytes(val_bytes, 'big', signed=False)
        else:
            val = val_bytes
        dps[dp_id] = val
        i += 4 + dp_len
    return dps

packets = reassemble(data["entries"])

# Build timeline of latest DP values
latest = {}
dp_timeline = []

for pkt in packets:
    raw = pkt["raw"]
    if len(raw) < 7 or raw[0] != 0x55 or raw[1] != 0xAA: continue
    cmd = raw[3]
    dlen = (raw[4] << 8) | raw[5]
    pdata = raw[6:6+dlen]
    if cmd in (0x06, 0x07, 0x22) and pdata:
        dps = parse_dp(pdata)
        for dp_id, val in dps.items():
            latest[dp_id] = val
            if dp_id in (106, 108, 109, 111):
                # Check if we have all 4 values
                if all(k in latest for k in (106, 108, 109, 111)):
                    solar_w = latest[106]
                    solar_pct = latest[108]
                    grid_pct = latest[109]
                    grid_w = latest[111]
                    total = solar_w + grid_w
                    if total > 0:
                        calc_solar_pct = round(solar_w / total * 100)
                        calc_grid_pct = round(grid_w / total * 100)
                        solar_pct_err = abs(calc_solar_pct - solar_pct)
                        grid_pct_err = abs(calc_grid_pct - grid_pct)
                        dp_timeline.append({
                            "ts": pkt["ts"][-12:],
                            "solar_w": solar_w,
                            "grid_w": grid_w,
                            "total_w": total,
                            "reported_solar_pct": solar_pct,
                            "reported_grid_pct": grid_pct,
                            "calc_solar_pct": calc_solar_pct,
                            "calc_grid_pct": calc_grid_pct,
                            "solar_err": solar_pct_err,
                            "grid_err": grid_pct_err,
                        })

print("=" * 120)
print("POWER MODEL VERIFICATION: DP106=Solar(W), DP111=Grid(W), DP108=Solar%, DP109=Grid%")
print("=" * 120)
print(f"\n{'Time':<14} {'Solar(W)':<10} {'Grid(W)':<10} {'Total(W)':<10} {'Rep S%':<8} {'Rep G%':<8} {'Calc S%':<8} {'Calc G%':<8} {'S% Err':<8} {'G% Err':<8}")
print("-" * 120)

total_solar_err = 0
total_grid_err = 0
count = 0

for entry in dp_timeline:
    print(f"{entry['ts']:<14} {entry['solar_w']:<10} {entry['grid_w']:<10} {entry['total_w']:<10} "
          f"{entry['reported_solar_pct']:<8} {entry['reported_grid_pct']:<8} "
          f"{entry['calc_solar_pct']:<8} {entry['calc_grid_pct']:<8} "
          f"{entry['solar_err']:<8} {entry['grid_err']:<8}")
    total_solar_err += entry['solar_err']
    total_grid_err += entry['grid_err']
    count += 1

if count > 0:
    print(f"\nAverage Solar % error: {total_solar_err/count:.2f}")
    print(f"Average Grid % error: {total_grid_err/count:.2f}")
    print(f"Total samples: {count}")
    max_solar_err = max(e['solar_err'] for e in dp_timeline)
    max_grid_err = max(e['grid_err'] for e in dp_timeline)
    print(f"Max Solar % error: {max_solar_err}")
    print(f"Max Grid % error: {max_grid_err}")

# Also verify energy counter rates
print("\n\n" + "=" * 80)
print("ENERGY COUNTER VERIFICATION")
print("=" * 80)

# Get DP107 (solar energy) and DP110 (total energy) timeline
dp107_vals = []
dp110_vals = []
latest2 = {}

for pkt in packets:
    raw = pkt["raw"]
    if len(raw) < 7 or raw[0] != 0x55 or raw[1] != 0xAA: continue
    cmd = raw[3]
    dlen = (raw[4] << 8) | raw[5]
    pdata = raw[6:6+dlen]
    if cmd in (0x22,) and pdata:
        dps = parse_dp(pdata)
        for dp_id, val in dps.items():
            if dp_id == 107:
                dp107_vals.append({"ts": pkt["ts"], "ts_ms": pkt["ts_ms"], "val": val})
            elif dp_id == 110:
                dp110_vals.append({"ts": pkt["ts"], "ts_ms": pkt["ts_ms"], "val": val})

if len(dp107_vals) >= 2:
    first, last = dp107_vals[0], dp107_vals[-1]
    delta_wh = last["val"] - first["val"]
    delta_s = (last["ts_ms"] - first["ts_ms"]) / 1000
    avg_w = delta_wh / (delta_s / 3600) if delta_s > 0 else 0
    print(f"\nDP 107 (hypothesized Solar Energy Counter):")
    print(f"  Start: {first['val']} Wh at {first['ts'][-12:]}")
    print(f"  End:   {last['val']} Wh at {last['ts'][-12:]}")
    print(f"  Delta: {delta_wh} Wh over {delta_s:.0f}s ({delta_s/60:.1f} min)")
    print(f"  Average power: {avg_w:.0f}W")
    print(f"  Lifetime solar: {first['val']/1000:.1f} kWh = {first['val']/1000000:.3f} MWh")

if len(dp110_vals) >= 2:
    first, last = dp110_vals[0], dp110_vals[-1]
    delta_wh = last["val"] - first["val"]
    delta_s = (last["ts_ms"] - first["ts_ms"]) / 1000
    avg_w = delta_wh / (delta_s / 3600) if delta_s > 0 else 0
    print(f"\nDP 110 (hypothesized Total Energy Counter):")
    print(f"  Start: {first['val']} Wh at {first['ts'][-12:]}")
    print(f"  End:   {last['val']} Wh at {last['ts'][-12:]}")
    print(f"  Delta: {delta_wh} Wh over {delta_s:.0f}s ({delta_s/60:.1f} min)")
    print(f"  Average power: {avg_w:.0f}W")
    print(f"  Lifetime total: {first['val']/1000:.1f} kWh = {first['val']/1000000:.3f} MWh")

    if dp107_vals:
        solar_lifetime = dp107_vals[0]["val"]
        total_lifetime = dp110_vals[0]["val"]
        print(f"\n  Solar fraction of lifetime: {solar_lifetime/total_lifetime*100:.1f}%")
