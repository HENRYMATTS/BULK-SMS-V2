# send_sms.py 
# --- RESPONSIBILITIES: Threading, Sending logic, Database Interaction ---

import serial
import time
import threading 
import os 
from collections import deque
from database import get_pending_messages, update_message_status, check_pending_count, clear_message_queue 
# At top of send_sms.py
import stats
import re
import random
from port_locks import get_port_lock

RESET_THRESHOLD = 3   # number of consecutive failures before software reset

# --- CONFIGURATION ---
SUCCESS_LOG_FILE = "sms_sent_log.csv"
FAILURE_LOG_FILE = "sms_failed_log.csv"

# --- GLOBAL SHARED RESOURCES ---
FAILED_QUEUE = deque() 
FAILED_QUEUE_LOCK = threading.Lock()
SUCCESS_LOCK = threading.Lock() 

# ------------------------------------------------------------------
# --- HELPER LOGGING FUNCTION ---
# ------------------------------------------------------------------

def log_to_file(filename, data):
    """Logs data to a file with thread safety."""
    with SUCCESS_LOCK:
        mode = 'a' if os.path.exists(filename) else 'w'
        try:
            with open(filename, mode) as f:
                if mode == 'w':
                    f.write("Timestamp,PhoneNumber,Status,Details\n")
                f.write(f"{data}\n")
        except Exception as e:
            print(f"!!! FILE LOGGING ERROR: {e}")


def software_reset_modem(ser, tty_path, label):
    """Perform a software reset on the modem and update network/signal stats."""
    try:
        # --- Existing reset code ---
        if ser and ser.isOpen():
            ser.write(b'AT+CFUN=1,1\r\n')
            time.sleep(0.5)
            ser.close()
        else:
            with serial.Serial(tty_path, baudrate=115200, timeout=5) as new_ser:
                new_ser.write(b'AT+CFUN=1,1\r\n')
                time.sleep(0.5)
        time.sleep(15)  # wait for reboot
                # --- Verify modem is responsive ---
        try:
            with serial.Serial(tty_path, baudrate=115200, timeout=5) as test_ser:
                test_ser.write(b'AT\r\n')
                time.sleep(0.5)
                resp = test_ser.read(100).decode(errors='ignore')
                if "OK" not in resp:
                    with stats.stats_lock:
                        stats.modem_stats[label]['last_error'] = "Modem unresponsive after reset"
                    return
        except Exception as e:
            with stats.stats_lock:
                stats.modem_stats[label]['last_error'] = f"AT check failed: {e}"
            return

        # --- Re-initialize basic settings ---
        with serial.Serial(tty_path, baudrate=115200, timeout=5) as new_ser:
            time.sleep(2)
            new_ser.write(b'AT+CMGF=1\r\n')
            time.sleep(0.5)
            new_ser.write(b'AT+GSMBUSY=1\r\n')
            time.sleep(0.5)
            new_ser.write(b'AT+CNMI=0,0,0,0\r\n')
            time.sleep(0.5)

        # --- NEW: Quick network and signal check after reset ---
        with serial.Serial(tty_path, baudrate=115200, timeout=5) as check_ser:
            time.sleep(1)  # settle
            # Network registration
            check_ser.write(b'AT+CREG?\r\n')
            time.sleep(0.5)
            resp = check_ser.read(100).decode(errors='ignore')
            match = re.search(r'\+CREG:\s*\d,\s*(\d)', resp)
            reg_status = match and match.group(1) in ('1', '5')
            # Signal strength
            check_ser.write(b'AT+CSQ\r\n')
            time.sleep(0.5)
            resp = check_ser.read(100).decode(errors='ignore')
            match = re.search(r'\+CSQ:\s*(\d+)', resp)
            if match:
                rssi = int(match.group(1))
                if rssi == 99:
                    signal = "unknown"
                else:
                    percent = min(100, round(rssi * 100 / 31))
                    signal = f"{percent}%"
            else:
                signal = None

        # --- Update stats ---
        with stats.stats_lock:
            stats.modem_stats[label]['network_registered'] = reg_status
            stats.modem_stats[label]['signal'] = signal
            stats.modem_stats[label]['reset_count'] += 1
            stats.modem_stats[label]['consecutive_failures'] = 0
            stats.modem_stats[label]['last_error'] = "Software reset performed"
    except Exception as e:
        with stats.stats_lock:
            stats.modem_stats[label]['last_error'] = f"Reset failed: {e}"


# ------------------------------------------------------------------
# --- WORKER FUNCTION ---
# ------------------------------------------------------------------

def send_data_worker(label: str, tty_path: str, message_obj: dict):
    lock = get_port_lock(tty_path)
    with lock:
        message_id = message_obj['id']
        phone_number = message_obj['phone_number']
        common_message = message_obj['message_body']
        current_attempt = message_obj['attempt_count']   # attempt count before this try

        ser_obj = None
        status = "FAILURE"
        details = "Unknown Error"
        timeout_occurred = False
        reset_detected = False

        try:
            ser_obj = serial.Serial(tty_path, baudrate=115200, timeout=16)
            time.sleep(2)
            ser_obj.flushInput()
            ser_obj.flushOutput()

            # --- Detect unscheduled reset by reading initial data ---
            initial = ser_obj.read(ser_obj.in_waiting).decode(errors='ignore')
            if 'RDY' in initial or 'SMS Ready' in initial:
                reset_detected = True
                with stats.stats_lock:
                    stats.modem_stats[label]['reset_count'] += 1

            # Modem commands sequence
            commands = [
                b"AT\r\n",
                b"AT+CMGF=1\r\n",
                f'AT+CMGS="{phone_number}"\r\n'.encode('utf-8'),
            ]

            for cmd in commands:
                ser_obj.write(cmd)
                time.sleep(random.uniform(0.3, 0.8))

            ser_obj.write(f"{common_message}\x1A".encode('utf-8'))
            ser_obj.flush()

            # Wait for response
            response = ser_obj.read_until(expected=b'OK\r\n', size=512).decode('utf-8', errors='ignore').strip()

            if "OK" in response:
                status = "SENT"
                details = "Success"
                # Update stats for success
                with stats.stats_lock:
                    s = stats.modem_stats[label]
                    s['sent'] += 1
                    if current_attempt == 0:
                        s['first_try_success'] += 1
                    else:
                        s['retry_success'] += 1
                    s['consecutive_failures'] = 0
                time.sleep(random.uniform(1.0, 3.0))

            else:
                status = "FAILED"
                details = f"Response: {response}"
                if "ERROR" in response:
                    # Could be specific error, but we treat as general failure
                    pass
                else:
                    timeout_occurred = True

               


        except serial.SerialTimeoutException:
            status = "FAILED"
            details = "Timeout"
            timeout_occurred = True
           
        except Exception as e:
            status = "FAILED"
            details = str(e)
            if "timeout" in str(e).lower():
                timeout_occurred = True

        finally:
            # Before closing, run diagnostics on failure to update network/signal
            if status != "SENT" and ser_obj and ser_obj.isOpen():
                try:
                    # Check network registration
                    ser_obj.write(b'AT+CREG?\r\n')
                    time.sleep(0.5)
                    resp = ser_obj.read(100).decode(errors='ignore')
                    match = re.search(r'\+CREG:\s*\d,\s*(\d)', resp)
                    if match:
                        reg_status = match.group(1) in ('1', '5')
                    else:
                        reg_status = False
                    # Check signal
                    ser_obj.write(b'AT+CSQ\r\n')
                    time.sleep(0.5)
                    resp = ser_obj.read(100).decode(errors='ignore')
                    match = re.search(r'\+CSQ:\s*(\d+)', resp)
                    if match:
                        rssi = int(match.group(1))
                        if rssi == 99:
                            signal = "unknown"
                        else:
                            percent = min(100, round(rssi * 100 / 31))
                            signal = f"{percent}%"
                    else:
                        signal = None
                    with stats.stats_lock:
                        s = stats.modem_stats[label]
                        s['network_registered'] = reg_status
                        s['signal'] = signal
                        s['last_error'] = details
                except Exception:
                    pass

                   # Update stats for failure
            if status != "SENT":
                with stats.stats_lock:
                    s = stats.modem_stats[label]
                    s['retry_attempts'] += 1
                    if timeout_occurred:
                        s['timeout_count'] += 1
                    s['consecutive_failures'] += 1
                    reset_needed = s['consecutive_failures'] >= RESET_THRESHOLD

                if reset_needed:
                    software_reset_modem(ser_obj, tty_path, label)
                    # Note: after reset, consecutive_failures is set to 0 inside the function

                ser_obj = None   # reset closed the port
                
                    # Update Database and get final status
            final_status = update_message_status(message_id, status, details)

            # If permanent failure, increment stats.failed
            if final_status == 'FAILED_PERMANENT':
                with stats.stats_lock:
                    stats.modem_stats[label]['failed'] += 1

            # File Logging
            log_data = f"{time.strftime('%Y-%m-%d %H:%M:%S')},{phone_number},{status},{details.replace(',', ';')}"
            log_to_file(SUCCESS_LOG_FILE if status == "SENT" else FAILURE_LOG_FILE, log_data)

            # If failed, add to FAILED_QUEUE (only if not permanent? but we still add, dispatcher will filter later)
            if status != "SENT":
                with FAILED_QUEUE_LOCK:
                    FAILED_QUEUE.append(message_obj)

            if ser_obj and ser_obj.isOpen():
                ser_obj.close()






# ------------------------------------------------------------------
# --- SMART DISPATCHER LOOP ---
# ------------------------------------------------------------------

def smart_parallel_dispatcher(tty_paths):
    """
    The main control loop. Manages batching and concurrent trigger.
    Now operates silently from a UI perspective to avoid circular imports.
    """
    num_devices = len(tty_paths)
    if num_devices == 0:
        return

    device_paths = list(tty_paths.items()) 

    while check_pending_count() > 0: 

        if stats.stop_requested:
            break

        if FAILED_QUEUE:
            with FAILED_QUEUE_LOCK:
                FAILED_QUEUE.clear() 

        batch_to_send = get_pending_messages(num_devices)
        if not batch_to_send:
            break

        threads = []
        for i, message_obj in enumerate(batch_to_send):
            # Only assign if we have a device for this batch index
            if i < len(device_paths):
                label, tty_path = device_paths[i]
                t = threading.Thread(
                    target=send_data_worker, 
                    args=(label, tty_path, message_obj)
                )
                threads.append(t) 

        for t in threads:
            t.start()
            
        for t in threads:
            t.join() 
            
    # Final cleanup handled by web_ui.py