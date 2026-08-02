"""
foreground_monitor.py — Foreground window process-name detection.

Polls GetForegroundWindow() every 300 ms, resolves the owning process name
via psutil, and compares it against the configured allowed-apps list.
The result is cached behind a threading.Lock for zero-contention reads
from the keyboard hook thread.
"""

import ctypes
import ctypes.wintypes as wintypes
import threading
import time

import psutil

from config import Config

# ------------------------------------------------------------------
# Windows API bindings
# ------------------------------------------------------------------
_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.DWORD),
]


class ForegroundMonitor:
    """Polls the foreground window and caches whether its process is allowed."""

    POLL_INTERVAL = 0.3  # seconds

    def __init__(self, config: Config):
        self._config = config
        self._is_allowed = False
        self._fg_name: str = ""
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self):
        """Start the polling thread (daemon, won't block exit)."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._poll, daemon=True, name="fg-monitor"
        )
        self._thread.start()

    def stop(self):
        """Signal the polling thread to exit."""
        self._running = False

    def is_allowed(self) -> bool:
        """Return the most recently cached allowed state."""
        with self._lock:
            return self._is_allowed

    def get_foreground_name(self) -> str:
        """Return the most recently cached foreground process name."""
        with self._lock:
            return self._fg_name

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _poll(self):
        while self._running:
            try:
                hwnd = _user32.GetForegroundWindow()
                if hwnd is None or hwnd == 0:
                    self._set_allowed(False, "")
                else:
                    pid = wintypes.DWORD()
                    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

                    proc = psutil.Process(pid.value)
                    app_name = proc.name()

                    allowed = self._config.get_allowed_apps()
                    new_state = app_name.lower() in (
                        a.lower() for a in allowed
                    )
                    self._set_allowed(new_state, app_name)

            except psutil.NoSuchProcess:
                # Process vanished — nothing to sync for
                self._set_allowed(False, "")
            except Exception:
                # AccessDenied (elevated app) or other failure —
                # keep last known fg_name but forbid mapping
                self._set_allowed(False, "")

            time.sleep(self.POLL_INTERVAL)

    def _set_allowed(self, v: bool, name: str = ""):
        with self._lock:
            self._is_allowed = v
            if name:
                self._fg_name = name
