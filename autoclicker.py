"""
autoclicker.py — Hold-to-repeat auto-clicker for 按键同步.

While the *physical* left mouse button is held down, inject rapid
left-click sequences at a configurable interval; releasing the button
stops. Respects the same gates as key mapping: master enabled switch,
auto-click enable switch, allowed-app whitelist, and chat-pause.

Why a WH_MOUSE_LL hook instead of polling GetAsyncKeyState:
- Injected clicks would flip GetAsyncKeyState to "up" right after each
  injection, so a poller would stop repeating.
- The hook sees physical vs injected events (LLMHF_INJECTED flag), so
  the press state is exact and immune to feedback loops.
"""

import ctypes
import ctypes.wintypes as wintypes
import threading
import time

import keyboard_hook  # is_chat_paused()
from config import Config
from foreground_monitor import ForegroundMonitor

# ------------------------------------------------------------------
# Windows API bindings
# ------------------------------------------------------------------
_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

WH_MOUSE_LL = 14
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_QUIT = 0x0012
LLMHF_INJECTED = 0x00000001

INPUT_MOUSE = 0
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]


_user32.SetWindowsHookExW.restype = ctypes.c_void_p
_user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
]
_user32.UnhookWindowsHookEx.restype = wintypes.BOOL
_user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
_user32.GetMessageW.restype = wintypes.BOOL
_user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG), ctypes.c_void_p,
    wintypes.UINT, wintypes.UINT,
]
_user32.TranslateMessage.restype = wintypes.BOOL
_user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
_user32.DispatchMessageW.restype = ctypes.c_long
_user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
_user32.PostThreadMessageW.restype = wintypes.BOOL
_user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
]
_user32.CallNextHookEx.restype = ctypes.c_long
_user32.CallNextHookEx.argtypes = [
    ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
]
_user32.SendInput.restype = wintypes.UINT
_user32.SendInput.argtypes = [
    wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int,
]

# ------------------------------------------------------------------
# Low-level mouse hook
# ------------------------------------------------------------------

# Global state written by the hook callback (runs on the hook thread),
# read by the click thread. Plain attribute access is atomic enough for
# a bool under CPython.
_pressed = False

_HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
)

# Keep the callback alive for the process lifetime — GC would kill the
# hook otherwise (same trap as the tray's WNDPROC).
_hook_proc_holder = None


def _hook_proc(nCode: int, wParam: int, lParam: int) -> int:
    global _pressed
    if nCode >= 0:
        struct = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT))
        if struct and struct.contents.flags & LLMHF_INJECTED:
            # Our own injected clicks — ignore, or we'd loop forever.
            return _user32.CallNextHookEx(None, nCode, wParam, lParam)
        if wParam == WM_LBUTTONDOWN:
            _pressed = True
        elif wParam == WM_LBUTTONUP:
            _pressed = False
    return _user32.CallNextHookEx(None, nCode, wParam, lParam)


def _inject_click(hold_s: float = 0.0):
    """Inject one left-button click via SendInput, as two separate events.

    down and up are sent in SEPARATE SendInput calls with a hold delay
    between them. Sending them back-to-back in one call (0 ms apart)
    fails for semi-automatic weapons: the game never observes a
    sustained trigger press, and because the physical button stays held,
    it concludes the trigger was never released → only the first shot
    fires. A real trigger cycle needs press → hold → release.
    """
    down = INPUT()
    down.type = INPUT_MOUSE
    down.u.mi.dwFlags = MOUSEEVENTF_LEFTDOWN

    up = INPUT()
    up.type = INPUT_MOUSE
    up.u.mi.dwFlags = MOUSEEVENTF_LEFTUP

    _user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
    if hold_s > 0:
        time.sleep(hold_s)
    _user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))


# ------------------------------------------------------------------
# AutoClicker
# ------------------------------------------------------------------

class AutoClicker:
    """Hold-to-repeat auto-clicker, gated by config + whitelist + pause."""

    def __init__(self, config: Config, monitor: ForegroundMonitor):
        self._config = config
        self._monitor = monitor
        self._running = False
        self._thread: threading.Thread | None = None  # hook thread
        self._click_thread: threading.Thread | None = None
        self._hook_handle: int | None = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="autoclicker-hook"
        )
        self._thread.start()
        self._click_thread = threading.Thread(
            target=self._click_loop, daemon=True, name="autoclicker-click"
        )
        self._click_thread.start()

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            # Wake the message loop so the hook thread can exit.
            _user32.PostThreadMessageW(self._thread.ident, WM_QUIT, 0, 0)
            self._thread.join(timeout=2.0)
        self._thread = None
        if self._click_thread and self._click_thread.is_alive():
            self._click_thread.join(timeout=2.0)
        self._click_thread = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run(self):
        global _hook_proc_holder
        _hook_proc_holder = _HOOKPROC(_hook_proc)

        self._hook_handle = _user32.SetWindowsHookExW(
            WH_MOUSE_LL, _hook_proc_holder, None, 0
        )
        try:
            # Message pump — required for low-level hook callbacks.
            msg = wintypes.MSG()
            while self._running:
                ret = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret <= 0:  # WM_QUIT → 0, error → -1
                    break
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if self._hook_handle:
                _user32.UnhookWindowsHookEx(self._hook_handle)
                self._hook_handle = None

    def _should_click(self) -> bool:
        if not self._running or not _pressed:
            return False
        if not self._config.is_enabled():
            return False
        if not self._config.is_autoclick_enabled():
            return False
        if keyboard_hook.is_chat_paused():
            return False
        if self._monitor is not None and not self._monitor.is_allowed():
            return False
        return True

    def _click_loop(self):
        interval = max(0.02, self._config.get_autoclick_interval() / 1000.0)
        # Trigger hold: long enough for the game to register a real press
        # (>= 1 frame), but short enough not to eat the whole interval.
        hold = min(0.015, max(0.005, interval * 0.25))
        while self._running:
            try:
                if self._should_click():
                    _inject_click(hold)
                    # Remaining time = release window: the game must see a
                    # sustained "button up" before the next press, or
                    # semi-automatic weapons won't re-fire.
                    time.sleep(max(0.005, interval - hold))
                else:
                    time.sleep(0.01)  # idle poll — respond to release fast
            except Exception:
                pass
