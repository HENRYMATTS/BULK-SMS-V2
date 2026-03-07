# hardware_init.py
#
# --- RESPONSIBILITIES: Physical Device Detection, AT Handshake, and Connection Preparation ---

import platform
import sys
import serial
import serial.tools.list_ports
import time
import os
import pyudev
import re
from collections import deque

# --- GLOBAL SHARED BUFFERS ---
# Stores (port_ui_id: str, color: str) tuples for buffered UI updates
PORT_STATUS_BUFFER = deque()
# Additional buffer for diagnostic log messages
DIAG_LOG_BUFFER = deque()

# --- CONFIGURATION ---
TARGET_VID = "214b"
TARGET_PID = "7250"

PHYSICAL_PORT_MAPPING = {
    '1': 'USB1',
    '2': 'USB2',
    '3': 'USB3',
    '4': 'USB4',
    '5': 'USB5',
    '6': 'USB6',
    '7': 'USB7',
    '8': 'USB8',
}

# ------------------------------------------------------------------
# --- DIAGNOSTIC FUNCTION ---
# ------------------------------------------------------------------

def diagnose_modem(label: str, tty_path: str):
    """Run a series of diagnostic commands on a modem and log results."""
    try:
        ser = serial.Serial(tty_path, baudrate=115200, timeout=3)
        time.sleep(1)
        ser.flushInput()
        ser.flushOutput()

        # Ensure text mode
        ser.write(b'AT+CMGF=1\r\n')
        time.sleep(0.5)
        resp = ser.read(100).decode(errors='ignore')
        if 'OK' not in resp:
            DIAG_LOG_BUFFER.append((label, "Failed to set text mode", 'red'))
        else:
            # 1. SIM status
            ser.write(b'AT+CPIN?\r\n')
            time.sleep(0.5)
            resp = ser.read(100).decode(errors='ignore')
            if '+CPIN: READY' in resp:
                DIAG_LOG_BUFFER.append((label, "SIM ready", 'green'))
            else:
                DIAG_LOG_BUFFER.append((label, f"SIM issue: {resp.strip()}", 'red'))

            # 2. Disable incoming calls (GSMBUSY)
            ser.write(b'AT+GSMBUSY=1\r\n')
            time.sleep(0.5)
            resp = ser.read(100).decode(errors='ignore')
            if 'OK' in resp:
                DIAG_LOG_BUFFER.append((label, "Incoming calls disabled", 'blue'))
            else:
                DIAG_LOG_BUFFER.append((label, "Failed to disable calls (unsupported?)", 'yellow'))

            # 3. Disable SMS notifications
            ser.write(b'AT+CNMI=0,0,0,0\r\n')
            time.sleep(0.5)
            resp = ser.read(100).decode(errors='ignore')
            if 'OK' in resp:
                DIAG_LOG_BUFFER.append((label, "SMS notifications disabled", 'blue'))
            else:
                DIAG_LOG_BUFFER.append((label, "Failed to disable SMS notifications", 'yellow'))

            # 4. Network registration
            ser.write(b'AT+CREG?\r\n')
            time.sleep(0.5)
            resp = ser.read(100).decode(errors='ignore')
            match = re.search(r'\+CREG:\s*\d,\s*(\d)', resp)
            if match:
                stat = match.group(1)
                if stat in ('1', '5'):
                    DIAG_LOG_BUFFER.append((label, "Network registered", 'green'))
                else:
                    DIAG_LOG_BUFFER.append((label, f"Network status: {stat} (not ready)", 'yellow'))
            else:
                DIAG_LOG_BUFFER.append((label, "Could not parse network registration", 'yellow'))

            # 5. Signal strength
            ser.write(b'AT+CSQ\r\n')
            time.sleep(0.5)
            resp = ser.read(100).decode(errors='ignore')
            match = re.search(r'\+CSQ:\s*(\d+)', resp)
            if match:
                rssi = int(match.group(1))
                if rssi == 99:
                    signal = "unknown"
                else:
                    percent = min(100, round(rssi * 100 / 31))
                    signal = f"{percent}%"
                DIAG_LOG_BUFFER.append((label, f"Signal strength: {signal}", 'blue'))
            else:
                DIAG_LOG_BUFFER.append((label, "Could not read signal", 'yellow'))


            # 6. Voltage (if supported)
            ser.write(b'AT+CBC\r\n')
            time.sleep(0.5)
            resp = ser.read_until(b'OK\r\n', 200).decode(errors='ignore')
            match = re.search(r'\+CBC:\s*\d+,\d+,(\d+)', resp)   # <-- fixed regex
            if match:
                voltage_mv = int(match.group(1))
                voltage_v = voltage_mv / 1000.0
                DIAG_LOG_BUFFER.append((label, f"Voltage: {voltage_v:.2f}V", 'blue'))
            else:
                DIAG_LOG_BUFFER.append((label, "Voltage info not available", 'yellow'))


            # 7. USSD balance check (Airtel Uganda)
            try:
                ser.write(b'AT+CUSD=1,"*131#",15\r\n')
                time.sleep(3)
                resp = ser.read(500).decode(errors='ignore')
                  # Terminate USSD session
                ser.write(b'AT+CUSD=2\r\n')
                time.sleep(0.5)
                ser.read(100)
                if '+CUSD:' in resp:
                    match = re.search(r'\+CUSD:\s*\d,\s*"([^"]+)"', resp)
                    if match:
                        balance_info = match.group(1)
                        DIAG_LOG_BUFFER.append((label, f"USSD balance: {balance_info}", 'blue'))
                    else:
                        DIAG_LOG_BUFFER.append((label, "USSD response format unrecognized", 'yellow'))
                elif 'ERROR' in resp:
                    DIAG_LOG_BUFFER.append((label, "USSD balance check failed (command error)", 'yellow'))
                else:
                    DIAG_LOG_BUFFER.append((label, "USSD balance check timed out", 'yellow'))
            except Exception as e:
                DIAG_LOG_BUFFER.append((label, f"USSD balance exception: {e}", 'red'))

            # 8. Clear all SMS
            ser.write(b'AT+CMGDA="DEL ALL"\r\n')
            time.sleep(1)
            resp = ser.read(200).decode(errors='ignore')
            if 'OK' in resp:
                DIAG_LOG_BUFFER.append((label, "All SMS cleared", 'green'))
            else:
                DIAG_LOG_BUFFER.append((label, "Failed to clear SMS", 'red'))

        ser.close()
    except Exception as e:
        DIAG_LOG_BUFFER.append((label, f"Diagnostic error: {e}", 'red'))

# ------------------------------------------------------------------
# --- BASIC AT TEST ---
# ------------------------------------------------------------------

def serial_devices(tty_path: str) -> bool:
    """
    Attempts to connect to the modem, sends 'AT', and actively waits for 'OK'.
    """
    ser_obj = None
    try:
        ser_obj = serial.Serial(tty_path, baudrate=115200, timeout=1)
        time.sleep(2)
        ser_obj.flushInput()
        ser_obj.flushOutput()
        ser_obj.write("AT\r\n".encode('utf-8'))
        ser_obj.flush()
        response = ser_obj.read_until(expected=b'OK\r\n', size=64).decode('utf-8', errors='ignore').strip()
        return "OK" in response
    except Exception:
        return False
    finally:
        if ser_obj and ser_obj.isOpen():
            ser_obj.close()

# ------------------------------------------------------------------
# --- MAIN HUB DETECTION (Linux only, pyudev) ---
# ------------------------------------------------------------------

def check_hub_ports():
    """
    Searches for and AT-tests all connected GSM modems/hubs using pyudev.
    Returns a dictionary of {label: tty_path} for ports that respond 'OK'.
    """
    global PORT_STATUS_BUFFER, DIAG_LOG_BUFFER
    PORT_STATUS_BUFFER.clear()
    DIAG_LOG_BUFFER.clear()

    # Reset all ports to white
    for os_id in PHYSICAL_PORT_MAPPING.keys():
        PORT_STATUS_BUFFER.append((f'p{os_id}', 'white'))

    if platform.system() != "Linux":
        print("Error: Linux required.")
        return {}

    try:
        ctx = pyudev.Context()
    except Exception as e:
        print(f"pyudev error: {e}")
        return {}

    all_tty_devices = list(ctx.list_devices(subsystem='tty'))
    ready_for_use = {}
    found_hub = False

    for dev in ctx.list_devices(subsystem="usb", DEVTYPE="usb_device"):
        vid = dev.get('ID_VENDOR_ID') or dev.get('idVendor')
        pid = dev.get('ID_MODEL_ID') or dev.get('idProduct')

        if vid and pid and vid.lower() == TARGET_VID and pid.lower() == TARGET_PID:
            found_hub = True
            hub_prefix = dev.sys_name + "."
            active_children_map = {}
            for child in dev.children:
                if child.sys_name.startswith(hub_prefix) and ":" not in child.sys_name:
                    os_port_id = child.sys_name.split('.')[-1]
                    active_children_map[os_port_id] = child

            for os_id in sorted(PHYSICAL_PORT_MAPPING.keys()):
                label = PHYSICAL_PORT_MAPPING[os_id]
                port_ui_id = f'p{os_id}'

                if os_id in active_children_map:
                    port_dev = active_children_map[os_id]
                    tty_node = None
                    for tty in all_tty_devices:
                        if port_dev.sys_path in tty.sys_path:
                            tty_node = tty.device_node
                            break

                    if tty_node:
                        if serial_devices(tty_node):
                            ready_for_use[label] = tty_node
                            PORT_STATUS_BUFFER.append((port_ui_id, 'green'))
                            diagnose_modem(label, tty_node)
                        else:
                            PORT_STATUS_BUFFER.append((port_ui_id, 'red'))
                    else:
                        PORT_STATUS_BUFFER.append((port_ui_id, 'red'))
                # else port empty, already white
            return ready_for_use

    if not found_hub:
        print("Hub not found.")
    return {}

# ------------------------------------------------------------------
# --- CONNECTION FUNCTION ---
# ------------------------------------------------------------------

def serial_connect(valid_ports_dict):
    """
    Simply returns the mapping of prepared paths.
    UI logging is handled by web_ui.py.
    """
    active_paths = {}
    for label, port_path in valid_ports_dict.items():
        active_paths[label] = port_path
    return active_paths