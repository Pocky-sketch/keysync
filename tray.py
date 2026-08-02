"""
tray.py — System tray icon via Shell_NotifyIconW (raw Windows API).

We avoid pystray to prevent threading conflicts: the keyboard hook and
tray icon each need their own GetMessageW message pump, and pystray's
internal loop doesn't compose well with our other ctypes-based threads.

The tray icon lives in its own daemon thread with a message-only window.
Left-click toggles the config window; right-click shows a context menu.
"""

import ctypes
import ctypes.wintypes as wintypes
import threading

from config import Config

# ------------------------------------------------------------------
# Windows API constants
# ------------------------------------------------------------------
NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2

NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04
NIF_INFO = 0x10

NIIF_INFO = 1
NIIF_NONE = 0

WM_USER = 0x0400
WM_TRAY_CALLBACK = WM_USER + 1
WM_COMMAND = 0x0111
WM_DESTROY = 0x0002
WM_QUIT = 0x0012
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_NULL = 0x0000

MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
MF_CHECKED = 0x00000008

TPM_LEFTALIGN = 0x0000
TPM_BOTTOMALIGN = 0x0002
TPM_RETURNCMD = 0x0100

CS_VREDRAW = 0x0001
CS_HREDRAW = 0x0002
WS_OVERLAPPEDWINDOW = 0x00CF0000
CW_USEDEFAULT = -2147483648
IDI_APPLICATION = 32512

# Menu command IDs
ID_SHOW_CONFIG = 1001
ID_TOGGLE_ENABLE = 1002
ID_QUIT = 1003
ID_ABOUT = 1004

# ------------------------------------------------------------------
# C structures
# ------------------------------------------------------------------


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", wintypes.INT),
        ("cbWndExtra", wintypes.INT),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HICON),  # HCURSOR ≡ HICON; wintypes has no HCURSOR
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


class POINT(ctypes.Structure):
    _fields_ = [
        ("x", wintypes.LONG),
        ("y", wintypes.LONG),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", wintypes.BYTE * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


# ------------------------------------------------------------------
# Windows type aliases (Python 3.14 wintypes omits some)
# ------------------------------------------------------------------
LRESULT = wintypes.LPARAM      # both are LONG_PTR
UINT_PTR = wintypes.WPARAM     # unsigned pointer-sized int

# ------------------------------------------------------------------
# Windows API function bindings
# ------------------------------------------------------------------
_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_shell32 = ctypes.windll.shell32
_gdi32 = ctypes.windll.gdi32

# Window class
_user32.RegisterClassExW.restype = wintypes.ATOM
_user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]

_user32.CreateWindowExW.restype = wintypes.HWND
_user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    wintypes.INT, wintypes.INT, wintypes.INT, wintypes.INT,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]

_user32.DefWindowProcW.restype = LRESULT
_user32.DefWindowProcW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
]

_user32.DestroyWindow.restype = wintypes.BOOL
_user32.DestroyWindow.argtypes = [wintypes.HWND]

# Tray icon
_shell32.Shell_NotifyIconW.restype = wintypes.BOOL
_shell32.Shell_NotifyIconW.argtypes = [
    wintypes.DWORD,
    ctypes.POINTER(NOTIFYICONDATAW),
]

# Menu
_user32.CreatePopupMenu.restype = wintypes.HMENU
_user32.AppendMenuW.restype = wintypes.BOOL
_user32.AppendMenuW.argtypes = [
    wintypes.HMENU, wintypes.UINT, UINT_PTR, wintypes.LPCWSTR,
]
_user32.CheckMenuItem.restype = wintypes.DWORD
_user32.CheckMenuItem.argtypes = [
    wintypes.HMENU, wintypes.UINT, wintypes.UINT,
]
_user32.TrackPopupMenu.restype = wintypes.BOOL
_user32.TrackPopupMenu.argtypes = [
    wintypes.HMENU, wintypes.UINT, wintypes.INT, wintypes.INT,
    wintypes.INT, wintypes.HWND, ctypes.c_void_p,
]
_user32.DestroyMenu.restype = wintypes.BOOL
_user32.DestroyMenu.argtypes = [wintypes.HMENU]
_user32.SetForegroundWindow.restype = wintypes.BOOL
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.PostMessageW.restype = wintypes.BOOL
_user32.PostMessageW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
]

# Cursor
_user32.GetCursorPos.restype = wintypes.BOOL
_user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]

# Icon creation
_user32.LoadIconW.restype = wintypes.HICON
_user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]

_user32.GetDC.restype = wintypes.HDC
_user32.GetDC.argtypes = [wintypes.HWND]

_user32.ReleaseDC.restype = wintypes.INT
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]

_user32.GetSysColor.restype = wintypes.DWORD
_user32.GetSysColor.argtypes = [wintypes.INT]

_user32.CreateIconIndirect.restype = wintypes.HICON
_user32.CreateIconIndirect.argtypes = [ctypes.c_void_p]  # PICONINFO

_gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
_gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]

_gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
_gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, wintypes.INT, wintypes.INT]

_gdi32.SetBitmapBits.restype = wintypes.LONG
_gdi32.SetBitmapBits.argtypes = [wintypes.HBITMAP, wintypes.DWORD, ctypes.c_void_p]

_gdi32.DeleteObject.restype = wintypes.BOOL
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]

# Message pump
_user32.GetMessageW.restype = wintypes.BOOL
_user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
]
_user32.TranslateMessage.restype = wintypes.BOOL
_user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
_user32.DispatchMessageW.restype = LRESULT
_user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]

_kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

# ------------------------------------------------------------------
# Globals (set by TrayIcon.__init__)
# ------------------------------------------------------------------
_config: Config | None = None
_show_callback = None  # callable to show/hide config window
_quit_callback = None  # callable to quit the whole app (tk mainloop)
_tray_wndproc_holder = None  # keep WNDPROC alive (ctypes GC guard)


def _create_simple_icon() -> wintypes.HICON:
    """Create a simple 16x16 icon programmatically using GDI.

    Draws a blue square with a white 'K' letter.
    """
    # We use a simple approach: create a small bitmap + use CreateIconIndirect
    # For simplicity, let's use LoadIcon with IDI_APPLICATION as fallback,
    # and try to create a nicer icon via Pillow if available.
    try:
        from PIL import Image, ImageDraw

        img = Image.new("RGBA", (32, 32), (255, 105, 180, 255))  # hot pink
        draw = ImageDraw.Draw(img)
        # Draw a white 'K' in the center
        draw.text((7, 4), "K", fill=(255, 255, 255, 255))
        # Also try to use a default font size
        return _pil_image_to_hicon(img)
    except Exception:
        pass

    # Fallback: use the standard application icon
    icon = _user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))
    if icon:
        return icon
    return wintypes.HICON(0)


def _pil_image_to_hicon(img) -> wintypes.HICON:
    """Convert a PIL Image to an HICON via GDI calls.

    Uses CreateDIBSection, draws the PIL image into the DC, then
    calls CreateIconIndirect.
    """
    import array

    # Ensure 32-bit RGBA
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    w, h = img.size
    pixels = img.tobytes("raw", "BGRA")  # Windows expects BGRA byte order

    # Create BITMAPINFO
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    bih = BITMAPINFOHEADER()
    bih.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bih.biWidth = w
    bih.biHeight = -h  # top-down DIB
    bih.biPlanes = 1
    bih.biBitCount = 32
    bih.biCompression = 0  # BI_RGB

    buf = ctypes.create_string_buffer(pixels, len(pixels))
    hdc = _user32.GetDC(None)
    hbm = _gdi32.CreateCompatibleBitmap(hdc, w, h)
    _gdi32.SetBitmapBits(hbm, len(pixels), ctypes.byref(buf))
    _user32.ReleaseDC(None, hdc)

    # CreateIconIndirect via ICONINFO
    class ICONINFO(ctypes.Structure):
        _fields_ = [
            ("fIcon", wintypes.BOOL),
            ("xHotspot", wintypes.DWORD),
            ("yHotspot", wintypes.DWORD),
            ("hbmMask", wintypes.HBITMAP),
            ("hbmColor", wintypes.HBITMAP),
        ]

    ii = ICONINFO()
    ii.fIcon = True
    ii.hbmColor = hbm
    ii.hbmMask = hbm
    hicon = _user32.CreateIconIndirect(ctypes.byref(ii))
    _gdi32.DeleteObject(hbm)
    return wintypes.HICON(hicon)


# ------------------------------------------------------------------
# Window procedure (called by Windows on the tray thread)
# ------------------------------------------------------------------

WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


def _tray_wndproc(hwnd, msg, wParam, lParam):
    if msg == WM_TRAY_CALLBACK:
        if lParam == WM_LBUTTONUP:
            # Left click → toggle config window
            if _show_callback:
                _show_callback()
        elif lParam == WM_RBUTTONUP:
            # Right click → context menu
            _show_context_menu(hwnd)

    elif msg == WM_COMMAND:
        cmd = wParam & 0xFFFF  # LOWORD
        if cmd == ID_SHOW_CONFIG:
            if _show_callback:
                _show_callback()
        elif cmd == ID_TOGGLE_ENABLE:
            if _config:
                _config.toggle_enabled()
        elif cmd == ID_QUIT:
            # Quit must also stop the tk mainloop on the main thread —
            # otherwise only the tray thread dies and the process becomes
            # a hidden zombie holding the single-instance mutex.
            if _quit_callback:
                _quit_callback()
            _user32.DestroyWindow(hwnd)

    elif msg == WM_DESTROY:
        _user32.PostQuitMessage(0)

    return _user32.DefWindowProcW(hwnd, msg, wParam, lParam)


def _show_context_menu(hwnd):
    """Create and show the right-click context menu."""
    hmenu = _user32.CreatePopupMenu()

    # Build menu with checkmark on Enable if enabled
    enabled = _config.is_enabled() if _config else True
    check = MF_CHECKED if enabled else 0

    _user32.AppendMenuW(hmenu, MF_STRING, ID_SHOW_CONFIG, "显示配置窗口")
    _user32.AppendMenuW(hmenu, MF_STRING | check, ID_TOGGLE_ENABLE, "启用同步")
    _user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
    _user32.AppendMenuW(hmenu, MF_STRING, ID_QUIT, "退出 KeySync")

    # Show at cursor position
    pt = POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    _user32.SetForegroundWindow(hwnd)  # required for TrackPopupMenu to work

    cmd = _user32.TrackPopupMenu(
        hmenu,
        TPM_LEFTALIGN | TPM_BOTTOMALIGN | TPM_RETURNCMD,
        pt.x, pt.y,
        0, hwnd, None,
    )

    # If the user selected a command via TrackPopupMenu return value,
    # post it so the wndproc handles it
    if cmd != 0:
        _user32.PostMessageW(hwnd, WM_COMMAND, cmd, 0)

    _user32.PostMessageW(hwnd, WM_NULL, 0, 0)
    _user32.DestroyMenu(hmenu)


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


class TrayIcon:
    """System tray icon running in its own daemon thread."""

    TIP_TEXT = "按键同步"

    def __init__(self, config: Config, show_callback, on_quit=None):
        global _config, _show_callback, _quit_callback
        _config = config
        _show_callback = show_callback
        _quit_callback = on_quit

        self._running = False
        self._thread: threading.Thread | None = None
        self._hwnd: wintypes.HWND | None = None
        self._hicon = _create_simple_icon()

    def start(self):
        """Create the tray icon and start the message pump."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="tray"
        )
        self._thread.start()

    def stop(self):
        """Remove the tray icon and quit the message pump."""
        self._running = False
        if self._hwnd:
            self._remove_tray_icon()
            _user32.PostMessageW(self._hwnd, WM_QUIT, 0, 0)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run(self):
        global _tray_wndproc_holder
        # 1. Register window class
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        # Keep the WNDPROC object alive for the thread's lifetime, and
        # cast it explicitly — Python 3.11+ ctypes rejects assigning a
        # WinFunctionType instance directly into a c_void_p field.
        _tray_wndproc_holder = WNDPROC(_tray_wndproc)
        wc.lpfnWndProc = ctypes.cast(_tray_wndproc_holder, ctypes.c_void_p)
        wc.hInstance = _kernel32.GetModuleHandleW(None)
        wc.lpszClassName = "KeySyncTrayClass"
        wc.hbrBackground = _gdi32.CreateSolidBrush(
            _user32.GetSysColor(1)
        )  # COLOR_BACKGROUND

        atom = _user32.RegisterClassExW(ctypes.byref(wc))
        if not atom:
            print("[KeySync] ERROR: RegisterClassExW failed")
            return

        # 2. Create message-only window (HWND_MESSAGE = -3)
        HWND_MESSAGE = wintypes.HWND(-3)
        self._hwnd = _user32.CreateWindowExW(
            0, "KeySyncTrayClass", "KeySyncTray", 0,
            0, 0, 0, 0, HWND_MESSAGE, None,
            _kernel32.GetModuleHandleW(None), None,
        )
        if not self._hwnd:
            print("[KeySync] ERROR: CreateWindowExW failed")
            return

        # 3. Add tray icon
        self._add_tray_icon()

        # 4. Message pump
        msg = wintypes.MSG()
        while self._running:
            ret = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret in (0, -1):
                break
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))

        # Cleanup
        self._remove_tray_icon()
        if self._hwnd:
            _user32.DestroyWindow(self._hwnd)
            self._hwnd = None

    def _add_tray_icon(self):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY_CALLBACK
        nid.hIcon = self._hicon
        nid.szTip = self.TIP_TEXT

        _shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

    def _remove_tray_icon(self):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
