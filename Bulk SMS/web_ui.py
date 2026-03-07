# web_ui.py
import eel
import os
import time
import threading 
import serial          # <-- added for balance check and clearing
import re              # <-- added for parsing
from typing import Dict, List
from collections import deque
from database import query_numbers_by_groups
from hardware_init import DIAG_LOG_BUFFER
# At top
import stats
import csv
from datetime import datetime
from database import connect_db   # add this
from port_locks import get_port_lock

# --- MODULE IMPORTS ---
from database import (
    setup_database, save_or_update_number_and_group, 
    check_pending_count, load_message_queue, clear_message_queue, 
    delete_number_and_associations, 
    get_job_status, update_job_status 
)

# NOTE: We only import the logic, we do NOT let hardware_init import us back.
from hardware_init import check_hub_ports, serial_connect, PORT_STATUS_BUFFER
from send_sms import smart_parallel_dispatcher 

import sys
import seed_test



@eel.expose
def get_groups_for_number(phone_number):
    """Return a list of group names for a given phone number, or empty list if not found."""
    try:
        from database import connect_db
        conn = connect_db()
        cursor = conn.cursor()
        # Find the number_id
        cursor.execute("SELECT id FROM Phone_Numbers WHERE phone_number = ?", (phone_number.strip(),))
        row = cursor.fetchone()
        if not row:
            return []
        number_id = row[0]
        # Get all group names for that number
        cursor.execute("""
            SELECT g.group_name
            FROM Groups g
            JOIN Group_Association ga ON g.id = ga.group_id
            WHERE ga.number_id = ?
        """, (number_id,))
        groups = [r[0] for r in cursor.fetchall()]
        conn.close()
        return groups
    except Exception as e:
        print(f"Error in get_groups_for_number: {e}")
        return []






def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller."""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def stats_updater():
    while True:
        time.sleep(2)
        with stats.stats_lock:
            current_stats = dict(stats.modem_stats)
        try:
            eel.js_update_stats_table(current_stats)()
        except:
            pass
        if not get_job_status().get('is_active') and check_pending_count() == 0:
            break


def seed_monitor():
    """Periodically check and run seed test based on message count."""
    log_serial_message("Seed monitor started", 'blue')
    while True:
        time.sleep(5)  # check every 5 seconds
        # Only run if a job is active
        if not get_job_status().get('is_active'):
            continue
        with ACTIVE_TTY_PATHS_LOCK:
            if not ACTIVE_TTY_PATHS:
                continue
            modems_copy = dict(ACTIVE_TTY_PATHS)
        try:
            seed_test.check_and_run_test(modems_copy)
        except Exception as e:
            log_serial_message(f"Seed monitor error: {e}", 'red')

def check_remaining_balances():
    """Query USSD balance for each active modem using per‑port locks."""
    balances = {}
    with ACTIVE_TTY_PATHS_LOCK:
        modems = dict(ACTIVE_TTY_PATHS)
    for label, tty_path in modems.items():
        lock = get_port_lock(tty_path)
        with lock:
            try:
                ser = serial.Serial(tty_path, baudrate=115200, timeout=5)
                time.sleep(2)
                ser.flushInput()
                ser.flushOutput()

                ser.write(b'AT+CMGF=1\r\n')
                time.sleep(0.5)
                ser.read(100)

                ser.write(b'AT+CUSD=1,"*131#",15\r\n')
                time.sleep(4)

                resp = ser.read(ser.in_waiting).decode(errors='ignore')
                resp += ser.read(500).decode(errors='ignore')
                ser.write(b'AT+CUSD=2\r\n')
                time.sleep(0.5)
                ser.read(100)
                ser.close()

                match = re.search(r'\+CUSD:\s*\d,\s*"([^"]+)"', resp, re.DOTALL)
                if match:
                    balance = match.group(1).replace('\r', ' ').replace('\n', ' ').strip()
                    if " . Dial" in balance:
                        balance = balance.split(" . Dial")[0].strip()
                    balances[label] = balance
                else:
                    balances[label] = "Balance check failed"
                    log_hardware_message(f"[{label}] USSD raw: {repr(resp)[:200]}", 'yellow')
            except Exception as e:
                balances[label] = f"Error: {e}"
                log_hardware_message(f"[{label}] USSD exception: {e}", 'red')
    return balances


def clear_all_modems_storage():
    """Clear all SMS from each active modem using per‑port locks."""
    with ACTIVE_TTY_PATHS_LOCK:
        modems = dict(ACTIVE_TTY_PATHS)
    for label, tty_path in modems.items():
        lock = get_port_lock(tty_path)
        with lock:
            try:
                ser = serial.Serial(tty_path, baudrate=115200, timeout=5)
                time.sleep(2)
                ser.flushInput()
                ser.flushOutput()
                ser.write(b'AT+CMGDA="DEL ALL"\r\n')
                time.sleep(1)
                resp = ser.read(200).decode(errors='ignore')
                ser.close()
                if 'OK' in resp:
                    log_serial_message(f"[{label}] Storage cleared", 'blue')
                else:
                    log_serial_message(f"[{label}] Clear failed", 'yellow')
            except Exception as e:
                log_serial_message(f"[{label}] Clear error: {e}", 'red')



def generate_final_report(balances=None):
    os.makedirs('reports', exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'reports/report_{timestamp}.csv'

    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM Message_Queue WHERE status='SENT'")
    total_sent = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM Message_Queue WHERE status='FAILED_PERMANENT'")
    total_failed = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM Message_Queue")
    total_loaded = cursor.fetchone()[0]
    conn.close()

    with stats.stats_lock:
        modem_stats_copy = dict(stats.modem_stats)

    try:
        with open(filename, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Report Generated', timestamp])
            writer.writerow([])
            writer.writerow(['Overall Totals'])
            writer.writerow(['Total Numbers Loaded', total_loaded])
            writer.writerow(['Successfully Sent', total_sent])
            writer.writerow(['Permanently Failed', total_failed])
            writer.writerow([])
            writer.writerow(['Per-Modem Statistics'])
            writer.writerow(['Modem', 'Sent', 'Failed (Perm)', 'Retry Attempts', 'First-Try Success', 'Retry Success', 'Timeouts', 'Resets', 'Network Registered', 'Signal', 'Last Error'])
            for modem, s in modem_stats_copy.items():
                writer.writerow([
                    modem,
                    s['sent'],
                    s['failed'],
                    s['retry_attempts'],
                    s['first_try_success'],
                    s['retry_success'],
                    s['timeout_count'],
                    s['reset_count'],
                    s['network_registered'],
                    s['signal'] or '',
                    s['last_error'] or ''
                ])
            # Add remaining balances section
            writer.writerow([])
            writer.writerow(['Remaining SMS Balances'])
            if balances:
                for modem, bal in balances.items():
                    writer.writerow([modem, bal])
            else:
                writer.writerow(['No balance info'])
        log_serial_message(f"Final report saved: {filename}", 'blue')
    except Exception as e:
        log_serial_message(f"❌ Failed to write final report: {e}", 'red')

# Define the web directory and set up the database
WEB_DIR = resource_path('web')

# --- GLOBAL STATE ---
ACTIVE_TTY_PATHS: Dict[str, str] = {} 
ACTIVE_TTY_PATHS_LOCK = threading.Lock()

# ------------------------------------------------------------------
# --- UI LOGGING HELPERS (unchanged) ---
# ------------------------------------------------------------------
def add_log_entry(element_id: str, message: str, color: str = 'black'):
    timestamp = time.strftime("[%H:%M:%S]")
    full_message = f'<span style="color:{color};">{timestamp} {message}</span>'
    try:
        eel.js_log_update(element_id, full_message)() 
    except Exception:
        pass

def log_serial_message(message: str, color: str = 'white'):
    add_log_entry('serial', message, color)

def log_hardware_message(message: str, color: str = 'white'):
    add_log_entry('hardware-log', message, color)

@eel.expose
def update_ui_status(status_color: str):
    try:
        eel.js_update_status(status_color)()
    except Exception:
        pass

def update_connect_button(is_connected: bool):
    try:
        eel.js_update_connect_button(is_connected)()
    except Exception:
        pass

def update_airtel_indicator(is_connected: bool):
    try:
        eel.js_update_airtel_indicator(is_connected)()
    except Exception:
        pass

# ------------------------------------------------------------------
# --- BUFFER PROCESSING ---
# ------------------------------------------------------------------
def process_port_status_buffer():
    if not PORT_STATUS_BUFFER:
        return
    while PORT_STATUS_BUFFER:
        try:
            port_id, color = PORT_STATUS_BUFFER.popleft() 
            time.sleep(0.01) 
            eel.js_update_port_color(port_id, color)() 
        except Exception as e:
            print(f"Error applying buffered update: {e}") 
            break

# ------------------------------------------------------------------
# --- THREADED HARDWARE DIAGNOSTICS (unchanged) ---
# ------------------------------------------------------------------
def run_hardware_diagnostic_thread():
    global ACTIVE_TTY_PATHS
    log_hardware_message("Initiating USB Hub Scan...", 'yellow')
    valid_ports = check_hub_ports() 
    process_port_status_buffer() 
    while DIAG_LOG_BUFFER:
        label, msg, color = DIAG_LOG_BUFFER.popleft()
        log_hardware_message(f"[{label}] {msg}", color)
    if not valid_ports:
        log_hardware_message("Scan Complete: No active modems found.", 'red')
        update_ui_status('red')
        update_connect_button(False)
        update_airtel_indicator(False) 
        return
    with ACTIVE_TTY_PATHS_LOCK:
        ACTIVE_TTY_PATHS = serial_connect(valid_ports)
    log_hardware_message(f"Success: {len(ACTIVE_TTY_PATHS)} modems ready.", 'green')
    log_serial_message(f"System connected to {len(ACTIVE_TTY_PATHS)} modems.", 'green')
    update_ui_status('green')
    update_connect_button(True)
    update_airtel_indicator(True)    

# ------------------------------------------------------------------
# --- EEL EXPOSED FUNCTIONS (unchanged except where noted) ---
# ------------------------------------------------------------------
@eel.expose
def check_initial_status():
    job_status = get_job_status()
    pending_count = check_pending_count()
    if job_status.get('is_active') and pending_count > 0:
        groups_str = job_status.get('group_names', 'N/A')
        log_serial_message(f"RECOVERY MODE: {pending_count} unsent messages found.", 'red')
        update_ui_status('red')
    else:
        log_serial_message("System Idle. Ready for hardware connection.", 'white') 
        update_ui_status('white')

@eel.expose
def connect_hardware():
    log_hardware_message("Starting hardware diagnostic...", 'yellow')
    update_ui_status('yellow') 
    threading.Thread(target=run_hardware_diagnostic_thread, daemon=True).start()
    return "SCANNING..."

@eel.expose
def start_bulk_send(groups_string: str, message_body: str) -> str:
    global ACTIVE_TTY_PATHS
    with ACTIVE_TTY_PATHS_LOCK:
        if not ACTIVE_TTY_PATHS:
            return "ERROR: No modems connected. Press CONNECT first."
        paths_copy = dict(ACTIVE_TTY_PATHS)

    job_status = get_job_status()
    is_recovery = job_status.get('is_active', 0) == 1
    
    if is_recovery:
        groups_str = job_status['group_names']
        msg_body = job_status['message_body']
        pending_count = load_message_queue(groups_str.split(','), msg_body, is_recovery=True)
        if pending_count == 0:
            update_job_status(False)
            update_ui_status('white')
            return "ERROR: Queue empty. Job status reset."
        log_message = f"RECOVERY STARTED: Resuming {pending_count} messages."
        result_message = "RECOVERY STARTED"
    else:
        if not groups_string or not message_body:
            return "ERROR: Groups and Message Body are required."
        groups_list = [g.strip() for g in groups_string.split('\n') if g.strip()]
        update_job_status(True, ", ".join(groups_list), message_body)
        loaded_count = load_message_queue(groups_list, message_body, is_recovery=False)
        if loaded_count == 0:
            update_job_status(False)
            return "ERROR: No numbers found."
        log_message = f"Dispatch Started: {loaded_count} messages."
        result_message = "Dispatch Started"

    # --- THE CLEANUP WRAPPER (updated) ---
    def dispatcher_with_cleanup():
        try:
            updater = threading.Thread(target=stats_updater, daemon=True)
            updater.start()
            smart_parallel_dispatcher(paths_copy)
            # Clear storage after sending
            clear_all_modems_storage()
            # Check remaining balances
            balances = check_remaining_balances()
            generate_final_report(balances)
            update_job_status(False)
            log_serial_message("✅ ALL MESSAGES PROCESSED. Job status cleared.", 'green')
            update_ui_status('green')
        except Exception as e:
            log_serial_message(f"❌ Dispatcher crashed: {e}. Job status preserved for recovery.", 'red')

    with stats.stats_lock:
        stats.modem_stats.clear()
        stats.stop_requested = False
    seed_test.reset_test_counter() 

    threading.Thread(target=dispatcher_with_cleanup, daemon=True).start()
   
    log_serial_message(log_message, 'green' if not is_recovery else 'red')
    return result_message

# ... (rest of the exposed functions unchanged: save_data_entry, delete_number_entry, check_group_count_py, stop_sending)

@eel.expose
def save_data_entry(phone_number: str, group_name: str) -> str:
    if not phone_number or not group_name: return "Error: Fields required."
    res = save_or_update_number_and_group(phone_number, group_name)
    log_serial_message(f"DB: {res}", 'yellow')
    return res

@eel.expose
def delete_number_entry(phone_number: str) -> str:
    if not phone_number: return "Error: Number required."
    res = delete_number_and_associations(phone_number)
    log_serial_message(f"DB: {res}", 'yellow')
    return res

@eel.expose
def check_group_count_py(groups_str):
    try:
        if not groups_str.strip():
            return "Please enter a group name."
        group_list = [g.strip() for g in groups_str.split(',') if g.strip()]
        if "ALL" in [g.upper() for g in group_list]:
            numbers = query_numbers_by_groups(group_list)
            count = len(numbers)
            return f"Total Unique Numbers: {count:,} (ALL groups combined)"
        per_group_counts = {}
        all_numbers = set()
        for group in group_list:
            group_numbers = query_numbers_by_groups([group])
            per_group_counts[group] = len(group_numbers)
            all_numbers.update([n[1] for n in group_numbers])
        combined = len(all_numbers)
        per_group_str = ", ".join([f"{g}: {c}" for g, c in per_group_counts.items()])
        return f"Total Unique Numbers: {combined:,} ({per_group_str})"
    except Exception as e:
        return f"Error checking count: {str(e)}"

@eel.expose
def stop_sending():
    stats.stop_requested = True
    log_serial_message("Stop signal sent – will stop after current batch.", 'yellow')
    return "STOPPING"

if __name__ == '__main__':
    setup_database()
    eel.init(WEB_DIR)
    threading.Thread(target=seed_monitor, daemon=True).start()
    try:
        eel.start('web_ui.html', mode='chrome-app', port=8090) 
    except Exception as e:
        print(f"Eel error: {e}")














