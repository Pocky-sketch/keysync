"""
main.py — 按键同步 entry point.

Orchestrates the config, foreground monitor, keyboard hook, config GUI,
and system tray icon. Enforces single-instance via a named mutex and
checks for admin rights (recommended for injecting into elevated apps).
"""

import ctypes
import ctypes.wintypes as wintypes
import os
import sys
import tkinter as tk

# Ensure the project directory is on sys.path so relative imports work
# when running as a script or from PyInstaller bundle
_proj_dir = os.path.dirname(os.path.abspath(__file__))
if _proj_dir not in sys.path:
    sys.path.insert(0, _proj_dir)

from config import Config
from foreground_monitor import ForegroundMonitor
from keyboard_hook import KeyboardHook
from autoclicker import AutoClicker
from gui import ConfigGUI
from tray import TrayIcon

# ------------------------------------------------------------------
# Single-instance enforcement via named mutex
# The handle is stored globally to prevent GC from releasing it.
# ------------------------------------------------------------------
MUTEX_NAME = "Global\\按键同步_SingleInstance"

_kernel32 = ctypes.windll.kernel32
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CreateMutexW.argtypes = [
    ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR,
]
_kernel32.GetLastError.restype = wintypes.DWORD
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

ERROR_ALREADY_EXISTS = 183

# Keep this handle alive for the process lifetime
_mutex_handle: wintypes.HANDLE | None = None


def _acquire_single_instance() -> bool:
    """Try to acquire the single-instance mutex.

    Returns True if this is the first instance, False if another
    instance is already running.
    """
    global _mutex_handle
    _mutex_handle = _kernel32.CreateMutexW(None, False, MUTEX_NAME)
    return _kernel32.GetLastError() != ERROR_ALREADY_EXISTS


# ------------------------------------------------------------------
# Admin check
# ------------------------------------------------------------------
_shell32 = ctypes.windll.shell32


def _is_admin() -> bool:
    """Return True if the process has administrator privileges."""
    return bool(_shell32.IsUserAnAdmin())


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

WM_CLOSE = 0x0010


def _make_tray_quit(gui):
    """Return a thread-safe quit callback for the tray thread.

    tkinter is not thread-safe — calling root.quit() from the tray
    thread does not stop the mainloop. Instead we post WM_CLOSE to the
    main window; tkinter handles it on the main thread and fires the
    WM_DELETE_WINDOW protocol (gui.quit), which ends the mainloop and
    lets the process exit cleanly.
    """
    def quit_from_tray():
        try:
            hwnd = gui.get_hwnd()
            ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        except Exception:
            # Last resort — direct call (may fail silently, but better
            # than a dead tray click).
            gui.quit()
    return quit_from_tray


def main():
    # --- Single-instance check ---
    if not _acquire_single_instance():
        # Find the existing 按键同步 process. Match both the old ASCII
        # name and the current Chinese name so leftover legacy instances
        # are still detected (defensive — the mutex name changed).
        import psutil
        old_pid = None
        for p in psutil.process_iter(['pid', 'cmdline']):
            try:
                cmd = ' '.join(p.info['cmdline'] or [])
                if 'KeySync' in cmd or '按键同步' in cmd:
                    old_pid = p.info['pid']
                    break
            except Exception:
                pass

        root = tk.Tk()
        root.withdraw()
        from tkinter import messagebox
        if old_pid:
            msg = f"按键同步已在运行中（PID: {old_pid}）。\n\n如需重启，请先在任务管理器中结束该进程。"
        else:
            msg = "按键同步已在运行中（系统托盘）。\n\n请查看系统托盘图标或任务管理器。"
        messagebox.showinfo("按键同步", msg)
        root.destroy()
        sys.exit(0)

    # --- High-DPI awareness (must be early, before tkinter init) ---
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    # --- Load config ---
    config = Config()

    # --- Admin warning (shown once only) ---
    if not _is_admin() and not config.admin_warning_shown():
        root = tk.Tk()
        root.withdraw()
        from tkinter import messagebox
        messagebox.showwarning(
            "按键同步 - 权限提示",
            "当前未以管理员身份运行。\n\n"
            "如果目标应用（如某些游戏）以管理员权限运行，\n"
            "按键同步可能无法生效。\n\n"
            "如需管理员权限：右键以管理员身份运行\n"
            "（Python 脚本或打包后的 exe 均可）。\n\n"
            "此提示只显示一次。",
        )
        root.destroy()
        config.mark_admin_warning_shown()

    # --- Start foreground monitor ---
    monitor = ForegroundMonitor(config)
    monitor.start()

    # --- Start keyboard hook ---
    hook = KeyboardHook(monitor, config)
    hook.start()

    # --- Start auto-clicker (hold left button to repeat clicks) ---
    autoclicker = AutoClicker(config, monitor)
    autoclicker.start()

    # --- Create GUI (the "real" tkinter root) ---
    gui = ConfigGUI(config, on_hotkey_changed=hook.refresh_autoclick_hotkey)

    # Config changes made behind the GUI's back (Pause hotkey, auto-click
    # hotkey, tray menu) → thread-safe GUI refresh so checkboxes/buttons
    # never drift from the real state.
    import keyboard_hook as _kh
    import autoclicker as _ac
    _kh._config_change_cb = gui.notify_config_changed
    _ac._config_change_cb = gui.notify_config_changed

    # --- Create tray icon ---
    def toggle_gui():
        if gui.is_visible():
            gui.hide()
        else:
            gui.show()

    tray = TrayIcon(config, toggle_gui, on_quit=_make_tray_quit(gui))
    tray.start()
    tray._config_change_cb = gui.notify_config_changed

    # --- Show GUI on first run ---
    if not config.has_run_before():
        gui.show()

    # --- Run GUI mainloop (blocks until quit) ---
    try:
        gui.run()
    except KeyboardInterrupt:
        pass
    finally:
        # Graceful shutdown
        tray.stop()
        autoclicker.stop()
        hook.stop()
        monitor.stop()


if __name__ == "__main__":
    main()
