# stats.py
import threading
from collections import defaultdict

# Lock for thread-safe access
stats_lock = threading.Lock()

# Per-modem statistics
modem_stats = defaultdict(lambda: {
    'sent': 0,
    'failed': 0,                # permanent failures
    'retry_attempts': 0,         # total number of retry attempts (failures that are not permanent yet)
    'first_try_success': 0,      # messages succeeded on first attempt
    'retry_success': 0,          # messages succeeded after at least one retry
    'timeout_count': 0,           # number of timeouts (subset of failures)
    'reset_count': 0,             # number of times modem reboot detected
    'network_registered': False,  # latest known network registration status
    'signal': None,               # latest signal strength percentage (string with '%')
    'last_error': None,           # last error message
    'consecutive_failures': 0 
})

# Global stop flag
stop_requested = False