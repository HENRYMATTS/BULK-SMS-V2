# seed_test.py
import serial
import time
import threading
import os
import stats
from port_locks import get_port_lock

_config = None
_config_lock = threading.Lock()
_last_test_sent = 0
_last_test_lock = threading.Lock()

def _log_message(msg, color='white'):
    # Simple console logging – no circular imports
    print(f"[SeedTest] {msg}")

def _load_config():
    config = {'seed': None, 'interval': 10}
    try:
        with open('config.txt', 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, val = line.split('=', 1)
                    key = key.strip().lower()
                    val = val.strip()
                    if key == 'seed':
                        config['seed'] = val
                    elif key == 'interval':
                        try:
                            config['interval'] = int(val)
                        except:
                            pass
    except FileNotFoundError:
        pass
    except Exception as e:
        _log_message(f"Config read error: {e}")
    return config

def get_config():
    global _config
    with _config_lock:
        if _config is None:
            _config = _load_config()
        return _config

def _send_seed_test(active_modems, seed):
    _log_message(f"Running seed test to {seed} on {len(active_modems)} modems")
    time.sleep(3)

    for label, tty_path in active_modems.items():
        lock = get_port_lock(tty_path)
        with lock:
            time.sleep(2)
            try:
                with stats.stats_lock:
                    sent_count = stats.modem_stats[label].get('sent', 0)

                ser = serial.Serial(tty_path, baudrate=115200, timeout=10)
                time.sleep(1)
                ser.flushInput()
                ser.flushOutput()

                ser.write(b'AT+CMGF=1\r\n')
                time.sleep(0.5)
                ser.read(100)

                ser.write(b'AT+CUSD=2\r\n')
                time.sleep(0.5)
                ser.read(100)

                ser.write(f'AT+CMGS="{seed}"\r\n'.encode())
                time.sleep(0.5)
                message = f'Seed test from {label} (sent: {sent_count})'
                ser.write(f'{message}\x1A'.encode())
                ser.flush()

                resp = ser.read_until(b'OK\r\n', size=200).decode(errors='ignore').strip()
                ser.close()

                if "OK" in resp:
                    _log_message(f"[{label}] Seed test sent successfully (count: {sent_count})")
                else:
                    _log_message(f"[{label}] Seed test failed: {resp}")
            except Exception as e:
                _log_message(f"[{label}] Seed test exception: {e}")
                try:
                    ser.close()
                except:
                    pass

def check_and_run_test(active_modems):
    global _last_test_sent
    config = get_config()
    seed = config.get('seed')
    interval = config.get('interval', 10)

    if not seed or not active_modems:
        return

    with stats.stats_lock:
        total_sent = sum(m.get('sent', 0) for m in stats.modem_stats.values())

    threshold = interval * len(active_modems)

    with _last_test_lock:
        if total_sent - _last_test_sent >= threshold:
            _last_test_sent = total_sent
            threading.Thread(target=_send_seed_test, args=(active_modems, seed), daemon=True).start()

def reset_test_counter():
    global _last_test_sent
    with _last_test_lock:
        _last_test_sent = 0