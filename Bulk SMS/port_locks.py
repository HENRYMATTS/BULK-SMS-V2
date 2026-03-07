# port_locks.py
import threading

_port_locks = {}
_port_locks_lock = threading.Lock()

def get_port_lock(tty_path: str) -> threading.Lock:
    with _port_locks_lock:
        if tty_path not in _port_locks:
            _port_locks[tty_path] = threading.Lock()
        return _port_locks[tty_path]