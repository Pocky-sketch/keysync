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
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_QUIT = 0x0012
LLMHF_INJECTED = 0x00000001

# XButton 标识 (MSLLHOOKSTRUCT.mouseData 高 16 位)
XBUTTON1 = 0x0001  # 侧键4 (后退)
XBUTTON2 = 0x0002  # 侧键5 (前进)

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
        ("dwExtraInfo", ctypes.c_size_t),  # ULONG_PTR — was wrongly POINTER(c_ulong)
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

# Windows 计时器分辨率: timeBeginPeriod(1) 把系统计时器粒度降到 1ms,
# time.sleep 才能精确到毫秒级 — 否则默认 15.6ms 粒度, 连点节奏会剧烈抖动。
_winmm = ctypes.windll.winmm
_winmm.timeBeginPeriod.restype = wintypes.UINT
_winmm.timeBeginPeriod.argtypes = [wintypes.UINT]
_winmm.timeEndPeriod.restype = wintypes.UINT
_winmm.timeEndPeriod.argtypes = [wintypes.UINT]

# 连点/钩子线程优先级 (参考 b1scoito/clicker 的 THREAD_PRIORITY_TIME_CRITICAL)
THREAD_PRIORITY_TIME_CRITICAL = 15
_kernel32.SetThreadPriority.restype = wintypes.BOOL
_kernel32.SetThreadPriority.argtypes = [
    ctypes.c_void_p, ctypes.c_int,
]
_kernel32.GetCurrentThread.restype = ctypes.c_void_p

_TIMER_PERIOD_MS = 1
_timer_period_count = 0  # 引用计数, 多组件共享计时器精度时安全


def _begin_timer_period():
    """Raise the Windows timer resolution to 1 ms (ref-counted)."""
    global _timer_period_count
    _timer_period_count += 1
    if _timer_period_count == 1:
        try:
            _winmm.timeBeginPeriod(_TIMER_PERIOD_MS)
        except Exception:
            pass


def _end_timer_period():
    """Restore the Windows timer resolution (ref-counted)."""
    global _timer_period_count
    if _timer_period_count > 0:
        _timer_period_count -= 1
        if _timer_period_count == 0:
            try:
                _winmm.timeEndPeriod(_TIMER_PERIOD_MS)
            except Exception:
                pass


def _boost_thread():
    """Raise the calling thread to TIME_CRITICAL (best-effort)."""
    try:
        _kernel32.SetThreadPriority(_kernel32.GetCurrentThread(),
                                    THREAD_PRIORITY_TIME_CRITICAL)
    except Exception:
        pass

# ------------------------------------------------------------------
# Low-level mouse hook
# ------------------------------------------------------------------

# Global state written by the hook callback (runs on the hook thread),
# read by the click thread. Plain attribute access is atomic enough for
# a bool under CPython.
_pressed = False
_injected_down = False  # hold mode: whether we're currently holding LEFT DOWN

# Module-level config reference so the hook callback (which has no
# instance access) can read the hotkey setting.
_config: Config | None = None

# Optional callback invoked after toggle_from_hotkey changes the
# auto-click enabled state. Set by main.py to the GUI's thread-safe
# notifier so the UI stays in sync when the hotkey toggles it.
_config_change_cb = None

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
        elif wParam == WM_XBUTTONDOWN:
            # Physical side-button press → hotkey toggle for the
            # auto-clicker. mouseData high word = which XButton.
            button = (struct.contents.mouseData >> 16) & 0xFFFF
            _on_side_button(button)
    return _user32.CallNextHookEx(None, nCode, wParam, lParam)


def _on_side_button(button: int):
    """Handle a physical side-button press — hotkey toggle if configured.

    XBUTTON1 → "mouse4", XBUTTON2 → "mouse5". Called on the hook thread.
    """
    if _config is None:
        return
    hotkey = _config.get_autoclick_hotkey().lower()
    expected = "mouse4" if button == XBUTTON1 else "mouse5" if button == XBUTTON2 else ""
    if expected and hotkey == expected:
        toggle_from_hotkey()


def toggle_from_hotkey():
    """Toggle the auto-clicker on/off (called by keyboard hotkey or mouse
    side button). Releases any held-down injection when turning off, so
    the game never gets a stuck button.
    """
    global _injected_down
    if _config is None:
        return
    new_state = _config.toggle_autoclick()
    if not new_state and _injected_down:
        try:
            _inject_up()
        except Exception:
            pass
        _injected_down = False
    if _config_change_cb:
        try:
            _config_change_cb()
        except Exception:
            pass


def _inject_down() -> bool:
    """Inject LEFT DOWN only (used by hold mode to keep the button pressed).

    Returns True if SendInput accepted the event (1 injected). False means
    the event was blocked — typically UIPI: the target game runs elevated
    (admin) while this process doesn't, or input is disabled on a secure
    desktop (UAC prompt / lock screen).
    """
    down = INPUT()
    down.type = INPUT_MOUSE
    down.u.mi.dwFlags = MOUSEEVENTF_LEFTDOWN
    return _user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT)) == 1


def _inject_up() -> bool:
    """Inject LEFT UP only (used by hold mode to release)."""
    up = INPUT()
    up.type = INPUT_MOUSE
    up.u.mi.dwFlags = MOUSEEVENTF_LEFTUP
    return _user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT)) == 1


def _inject_click(hold_s: float = 0.0) -> bool:
    """Inject one left-button click via SendInput, as two separate events.

    down and up are sent in SEPARATE SendInput calls with a hold delay
    between them. Sending them back-to-back in one call (0 ms apart)
    fails for semi-automatic weapons: the game never observes a
    sustained trigger press, and because the physical button stays held,
    it concludes the trigger was never released → only the first shot
    fires. A real trigger cycle needs press → hold → release.
    """
    if not _inject_down():
        return False
    if hold_s > 0:
        time.sleep(hold_s)
    return _inject_up()


# ------------------------------------------------------------------
# Injection failure detection
# ------------------------------------------------------------------

# SendInput failing continuously means the game is likely running at a
# higher integrity level (admin) than us, or input is disabled — keep
# clicking would be pointless, so after a threshold we stop injecting
# and raise a user-visible warning (once per failure streak).
_failure_threshold = 20
_failure_count = 0
_failure_reported = False
_failure_cb = None  # set by main.py: called (on any thread) when a streak ends


def _note_injection_result(ok: bool):
    """Track SendInput success/failure; fire _failure_cb on a long streak.

    _failure_cb(blocked: bool) is called once when a failure streak
    crosses the threshold, and again (blocked=False) when injections
    recover — so the GUI can show and clear the warning.
    """
    global _failure_count, _failure_reported
    if ok:
        _failure_count = 0
        if _failure_reported:
            _failure_reported = False
            if _failure_cb:
                try:
                    _failure_cb(False)
                except Exception:
                    pass
        return
    _failure_count += 1
    if _failure_count >= _failure_threshold and not _failure_reported:
        _failure_reported = True
        if _failure_cb:
            try:
                _failure_cb(True)
            except Exception:
                pass


# ------------------------------------------------------------------
# AutoClicker
# ------------------------------------------------------------------

def _timing(interval_ms: int) -> tuple[float, float]:
    """Derive (interval_s, hold_s) from the configured interval in ms.

    hold = half the interval (press cycle is down→hold→up, matching a
    real click where the button is physically down ~50% of the time —
    same ratio b1scoito/clicker uses). Clamped so very fast intervals
    keep a minimum hold (games still need >=1 frame of press) and slow
    intervals don't feel like a drag (max 40 ms).
    """
    interval = max(20, min(500, int(interval_ms))) / 1000.0
    hold = min(0.040, max(0.008, interval * 0.5))
    return interval, hold


class AutoClicker:
    """Hold-to-repeat auto-clicker, gated by config + whitelist + pause."""

    def __init__(self, config: Config, monitor: ForegroundMonitor):
        global _config
        self._config = config
        _config = config  # module-level ref for the hook callback
        self._monitor = monitor
        self._running = False
        self._thread: threading.Thread | None = None  # hook thread
        self._click_thread: threading.Thread | None = None
        self._hook_handle: int | None = None

    def start(self):
        global _failure_count, _failure_reported
        if self._running:
            return
        self._running = True
        _failure_count = 0
        _failure_reported = False
        _begin_timer_period()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="autoclicker-hook"
        )
        self._thread.start()
        self._click_thread = threading.Thread(
            target=self._click_loop, daemon=True, name="autoclicker-click"
        )
        self._click_thread.start()

    def stop(self):
        global _injected_down
        self._running = False
        # Release any held-down injection (hold mode) so the game doesn't
        # get a stuck button.
        if _injected_down:
            try:
                _inject_up()
            except Exception:
                pass
            _injected_down = False
        if self._thread and self._thread.is_alive():
            # Wake the message loop so the hook thread can exit.
            _user32.PostThreadMessageW(self._thread.ident, WM_QUIT, 0, 0)
            self._thread.join(timeout=2.0)
        self._thread = None
        if self._click_thread and self._click_thread.is_alive():
            self._click_thread.join(timeout=2.0)
        self._click_thread = None
        _end_timer_period()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run(self):
        global _hook_proc_holder
        _hook_proc_holder = _HOOKPROC(_hook_proc)
        _boost_thread()

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
        _boost_thread()
        # NOTE: interval/hold are re-read on EVERY iteration so config
        # changes (GUI settings) take effect immediately — previously
        # they were cached once before the loop, so changing the interval
        # required restarting the whole app.
        while self._running:
            try:
                interval, hold = _timing(self._config.get_autoclick_interval())
                if self._config.get_autoclick_mode() == "hold":
                    self._hold_cycle()
                elif self._should_click():
                    ok = _inject_click(hold)
                    _note_injection_result(ok)
                    if ok:
                        # Remaining time = release window: the game must
                        # see a sustained "button up" before the next
                        # press, or semi-automatic weapons won't re-fire.
                        time.sleep(max(0.005, interval - hold))
                    else:
                        # Injected clicks are being blocked (admin game,
                        # locked desktop…) — stop hammering, just idle.
                        time.sleep(0.05)
                else:
                    time.sleep(0.01)  # idle poll — respond to release fast
            except Exception:
                pass

    def _hold_cycle(self):
        """Hold mode: mirror the physical button press.

        Physical down → inject a held LEFT DOWN (and keep it); physical
        up → inject LEFT UP. No down/up cycling, so full-auto weapons
        keep their natural fire rate instead of being chopped by rapid
        re-triggers.
        """
        global _injected_down
        if self._should_click():
            if not _injected_down:
                _note_injection_result(_inject_down())
                _injected_down = True
            time.sleep(0.01)
        else:
            if _injected_down:
                _note_injection_result(_inject_up())
                _injected_down = False
            time.sleep(0.01)
