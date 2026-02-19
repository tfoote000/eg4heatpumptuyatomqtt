#!/usr/bin/env python3
"""
Dual UART Sniffer - Raspberry Pi 3B
Robust version with proper buffer management
"""

import serial
import threading
import time
import json
from datetime import datetime
from collections import deque

# UART Configuration
TUYA_PORT = '/dev/ttyAMA0'
MCU_PORT = '/dev/ttyUSB0'
BAUD_RATE = 9600

# Logging
TIMESTAMP_FORMAT = datetime.now().strftime('%Y%m%d_%H%M%S')
LOG_FILENAME = f"uart_capture_{TIMESTAMP_FORMAT}.log"
JSON_FILENAME = f"uart_capture_{TIMESTAMP_FORMAT}.json"

# Capture settings
RAW_BYTE_TIMEOUT = 0.05  # 50ms timeout for grouping bytes
MAX_PACKET_SIZE = 1024
FLUSH_INTERVAL = 5  # Flush JSON every 5 seconds

# Thread-safe logging with queue
json_lock = threading.Lock()
json_data = []
last_flush_time = time.time()

class SmartBuffer:
    """Captures both Tuya packets AND raw byte streams"""
    def __init__(self, name):
        self.name = name
        self.buffer = bytearray()
        self.last_byte_time = time.time()
    
    def add_byte(self, byte):
        """Add byte and check for complete messages"""
        current_time = time.time()
        self.buffer.append(byte)
        
        # Check for Tuya packet (0x55 0xAA)
        if len(self.buffer) >= 2 and self.buffer[0] == 0x55 and self.buffer[1] == 0xAA:
            if len(self.buffer) >= 6:
                try:
                    length = (self.buffer[4] << 8) | self.buffer[5]
                    expected_len = 6 + length + 1
                    
                    # Sanity check on length
                    if expected_len > MAX_PACKET_SIZE:
                        # Invalid length, treat as raw stream
                        raw_data = bytes(self.buffer)
                        self.buffer = bytearray()
                        self.last_byte_time = current_time
                        return ('raw_stream', raw_data)
                    
                    if len(self.buffer) >= expected_len:
                        packet = bytes(self.buffer[:expected_len])
                        self.buffer = bytearray(self.buffer[expected_len:])  # Keep excess
                        self.last_byte_time = current_time
                        return ('tuya_packet', packet)
                except Exception as e:
                    # Error parsing, dump as raw
                    raw_data = bytes(self.buffer)
                    self.buffer = bytearray()
                    self.last_byte_time = current_time
                    return ('raw_stream', raw_data)
        
        # Check for timeout (raw byte stream)
        time_since_last = current_time - self.last_byte_time
        
        # If timeout OR buffer getting too large, flush it
        if (time_since_last > RAW_BYTE_TIMEOUT and len(self.buffer) > 0) or len(self.buffer) > MAX_PACKET_SIZE:
            raw_data = bytes(self.buffer)
            self.buffer = bytearray()
            self.last_byte_time = current_time
            return ('raw_stream', raw_data)
        
        self.last_byte_time = current_time
        return None
    
    def check_timeout(self):
        """Check if buffer should be flushed due to timeout"""
        if len(self.buffer) > 0:
            time_since_last = time.time() - self.last_byte_time
            if time_since_last > RAW_BYTE_TIMEOUT:
                raw_data = bytes(self.buffer)
                self.buffer = bytearray()
                return ('raw_stream', raw_data)
        return None

def format_hex(data):
    """Format bytes as hex string"""
    return ' '.join(f'{b:02X}' for b in data)

def format_ascii(data):
    """Format bytes as ASCII (printable only)"""
    return ''.join(chr(b) if 32 <= b < 127 else '.' for b in data)

def timestamp():
    """Get current timestamp"""
    return datetime.now().strftime('%H:%M:%S.%f')[:-3]

def timestamp_iso():
    """Get ISO format timestamp for JSON"""
    return datetime.now().isoformat()

def log_message(msg):
    """Print to console and write to text log file"""
    print(msg, flush=True)  # Force flush
    try:
        with open(LOG_FILENAME, 'a') as f:
            f.write(msg + '\n')
    except Exception as e:
        print(f"Warning: Could not write to log: {e}")

def log_json_entry(source, data_type, raw_bytes, decoded_info=None):
    """Log entry to JSON array (thread-safe)"""
    global json_data, last_flush_time
    
    entry = {
        "timestamp": timestamp_iso(),
        "timestamp_ms": time.time() * 1000,
        "source": source,
        "type": data_type,
        "raw_hex": format_hex(raw_bytes),
        "raw_bytes": list(raw_bytes),
        "length": len(raw_bytes),
        "ascii": format_ascii(raw_bytes)
    }
    
    if decoded_info:
        entry["decoded"] = decoded_info
    
    with json_lock:
        json_data.append(entry)
        
        # Time-based flush instead of count-based
        current_time = time.time()
        if current_time - last_flush_time > FLUSH_INTERVAL:
            save_json()
            last_flush_time = current_time

def save_json():
    """Save JSON data to file (must be called with lock held or from main thread)"""
    try:
        with open(JSON_FILENAME, 'w') as f:
            json.dump({
                "capture_info": {
                    "start_time": TIMESTAMP_FORMAT,
                    "tuya_port": TUYA_PORT,
                    "mcu_port": MCU_PORT,
                    "baud_rate": BAUD_RATE
                },
                "entries": json_data
            }, f, indent=2)
    except Exception as e:
        print(f"Warning: JSON save error: {e}")

def decode_tuya_packet(packet):
    """Decode Tuya packet structure"""
    if len(packet) < 7:
        return {"valid": False, "error": "Too short"}
    
    try:
        version = packet[2]
        command = packet[3]
        length = (packet[4] << 8) | packet[5]
        data = packet[6:6+length] if len(packet) > 6 else b''
        checksum = packet[-1]
        
        cmd_names = {
            0x00: "Heartbeat", 0x01: "Query Product", 0x02: "Query MCU",
            0x03: "Report Network", 0x04: "Reset WiFi", 0x05: "Reset Select",
            0x06: "Send Command", 0x07: "Status Report", 0x08: "Query State",
            0x09: "Upload Firmware", 0x0A: "Get Local Time",
        }
        
        decoded = {
            "valid": True,
            "version": version,
            "command": command,
            "command_name": cmd_names.get(command, f"Unknown(0x{command:02X})"),
            "data_length": length,
            "checksum": checksum,
            "checksum_hex": f"0x{checksum:02X}"
        }
        
        if data:
            decoded["data_hex"] = format_hex(data)
            decoded["data_bytes"] = list(data)
            # Try ASCII decode
            if all(32 <= b < 127 for b in data):
                try:
                    decoded["data_ascii"] = data.decode('ascii')
                except:
                    pass
        
        return decoded
    except Exception as e:
        return {"valid": False, "error": str(e)}

def format_display(data_type, data, decoded=None):
    """Format for console display"""
    if data_type == 'tuya_packet' and decoded and decoded.get("valid"):
        result = f"{decoded['command_name']} | Len:{decoded['data_length']}"
        if "data_hex" in decoded:
            # Truncate long data for display
            data_hex = decoded['data_hex']
            if len(data_hex) > 60:
                data_hex = data_hex[:60] + "..."
            result += f" | Data: {data_hex}"
        result += f" | Chk:{decoded['checksum_hex']}"
        return result
    elif data_type == 'raw_stream':
        ascii_str = format_ascii(data)
        if len(ascii_str) > 40:
            ascii_str = ascii_str[:40] + "..."
        return f"RAW | ASCII: [{ascii_str}]"
    else:
        return "UNKNOWN"

def monitor_uart(port_name, serial_port, label, source_name):
    """Monitor a single UART port"""
    log_message(f"[{timestamp()}] {label} started on {port_name}")
    buffer = SmartBuffer(label)
    consecutive_errors = 0
    
    try:
        while True:
            try:
                # Non-blocking read with small timeout
                if serial_port.in_waiting > 0:
                    # Read available bytes (up to 256 at a time)
                    bytes_to_read = min(serial_port.in_waiting, 256)
                    data = serial_port.read(bytes_to_read)
                    
                    # Process each byte
                    for byte in data:
                        result = buffer.add_byte(byte)
                        if result:
                            process_result(result, label, source_name)
                    
                    consecutive_errors = 0  # Reset error counter
                else:
                    # Check for timeout flush
                    result = buffer.check_timeout()
                    if result:
                        process_result(result, label, source_name)
                    
                    time.sleep(0.001)  # Small sleep to prevent CPU spinning
                    
            except serial.SerialException as e:
                consecutive_errors += 1
                log_message(f"[{timestamp()}] {label} Serial error: {e}")
                if consecutive_errors > 10:
                    log_message(f"[{timestamp()}] {label} Too many errors, stopping")
                    break
                time.sleep(0.1)
            except Exception as e:
                log_message(f"[{timestamp()}] {label} Error: {e}")
                time.sleep(0.1)
                
    except Exception as e:
        log_message(f"[{timestamp()}] {label} FATAL: {e}")

def process_result(result, label, source_name):
    """Process a complete message (packet or raw stream)"""
    try:
        data_type, data = result
        
        # Decode if Tuya packet
        decoded = None
        if data_type == 'tuya_packet':
            decoded = decode_tuya_packet(data)
        
        # Log to JSON (async)
        log_json_entry(source_name, data_type, data, decoded)
        
        # Display to console
        hex_str = format_hex(data)
        display_str = format_display(data_type, data, decoded)
        
        log_message(f"[{timestamp()}] {label} [{len(data):3d} bytes] {hex_str}")
        log_message(f"{'':26s} └─ {display_str}")
        
    except Exception as e:
        log_message(f"Error processing result: {e}")

def main():
    global last_flush_time
    
    print("=" * 75)
    print("          Dual UART Sniffer - Raspberry Pi 3B")
    print("          Robust Capture (All Data)")
    print("=" * 75)
    print(f"  Tuya Port: {TUYA_PORT}")
    print(f"  MCU Port:  {MCU_PORT}")
    print(f"  Baud Rate: {BAUD_RATE}")
    print(f"  Text Log:  {LOG_FILENAME}")
    print(f"  JSON Log:  {JSON_FILENAME}")
    print("=" * 75)
    
    try:
        # Larger buffer sizes
        tuya_serial = serial.Serial(
            port=TUYA_PORT,
            baudrate=BAUD_RATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.01,
            write_timeout=0.01,
            inter_byte_timeout=None
        )
        # Increase buffer size if supported
        try:
            tuya_serial.set_buffer_size(rx_size=4096)
        except:
            pass
        
        print(f"  ✓ Opened {TUYA_PORT}")
        
        mcu_serial = serial.Serial(
            port=MCU_PORT,
            baudrate=BAUD_RATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.01,
            write_timeout=0.01,
            inter_byte_timeout=None
        )
        try:
            mcu_serial.set_buffer_size(rx_size=4096)
        except:
            pass
        
        print(f"  ✓ Opened {MCU_PORT}")
        
    except serial.SerialException as e:
        print(f"\n  ✗ ERROR: {e}\n")
        return
    
    print("\n  Monitoring... Press Ctrl+C to stop\n")
    print("=" * 75 + "\n")
    
    # Start threads
    tuya_thread = threading.Thread(
        target=monitor_uart,
        args=(TUYA_PORT, tuya_serial, "[T→M]", "tuya_to_mcu"),
        daemon=True,
        name="TuyaMonitor"
    )
    
    mcu_thread = threading.Thread(
        target=monitor_uart,
        args=(MCU_PORT, mcu_serial, "[M→T]", "mcu_to_tuya"),
        daemon=True,
        name="MCUMonitor"
    )
    
    tuya_thread.start()
    mcu_thread.start()
    
    log_message(f"[{timestamp()}] === Monitoring Started ===\n")
    last_flush_time = time.time()
    
    # Main loop - periodic JSON flush
    try:
        while True:
            time.sleep(1)
            
            # Periodic flush
            current_time = time.time()
            if current_time - last_flush_time > FLUSH_INTERVAL:
                with json_lock:
                    save_json()
                last_flush_time = current_time
            
            # Check if threads are alive
            if not tuya_thread.is_alive():
                log_message(f"[{timestamp()}] WARNING: Tuya thread died!")
            if not mcu_thread.is_alive():
                log_message(f"[{timestamp()}] WARNING: MCU thread died!")
                
    except KeyboardInterrupt:
        print("\n" + "=" * 75)
        print("  Stopping...")
        print("=" * 75)
        
        tuya_serial.close()
        mcu_serial.close()
        
        # Final save
        with json_lock:
            save_json()
        
        print(f"\n  ✓ Text log: {LOG_FILENAME}")
        print(f"  ✓ JSON log: {JSON_FILENAME}")
        print(f"  ✓ Total entries: {len(json_data)}\n")

if __name__ == '__main__':
    main()