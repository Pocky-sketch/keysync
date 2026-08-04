"""
process_picker.py — Graphical process selection for 按键同步.

Provides two ways to pick a process:
1. ProcessListDialog — a searchable list of all running processes
2. pick_window_by_click() — click on any window to identify its process
"""

import ctypes
import ctypes.wintypes as wintypes
import tkinter as tk
from tkinter import ttk, messagebox

import psutil

# ------------------------------------------------------------------
# Windows API for enumerating windows + window picker
# ------------------------------------------------------------------
_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

# EnumWindows callback type
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

_user32.EnumWindows.restype = wintypes.BOOL
_user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]

_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = [wintypes.HWND]

_user32.GetWindowTextLengthW.restype = wintypes.INT
_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]

_user32.GetWindowTextW.restype = wintypes.INT
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, wintypes.INT]

_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.DWORD),
]

_user32.GetForegroundWindow.restype = wintypes.HWND

_user32.GetWindowLongW.restype = wintypes.LONG
_user32.GetWindowLongW.argtypes = [wintypes.HWND, wintypes.INT]

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000


# ------------------------------------------------------------------
# Window enumeration helpers
# ------------------------------------------------------------------

# Module-level list for EnumWindows callback (avoids ctypes LPARAM cast
# issues with py_object on Python 3.14).
_enum_windows_result: list[dict] = []


def _window_enum_proc(hwnd, lParam):
    """EnumWindows callback — collects (hwnd, title) of visible main windows."""
    # lParam is ignored; we use the module-level _enum_windows_result

    if not _user32.IsWindowVisible(hwnd):
        return True

    # Skip tool windows (no taskbar entry, typically helper windows)
    ex_style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    if ex_style & WS_EX_TOOLWINDOW:
        return True

    title_len = _user32.GetWindowTextLengthW(hwnd)
    if title_len == 0:
        return True  # skip titleless windows

    buf = ctypes.create_unicode_buffer(title_len + 1)
    _user32.GetWindowTextW(hwnd, buf, title_len + 1)
    title = buf.value
    if not title or not title.strip():
        return True

    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

    _enum_windows_result.append({
        "hwnd": hwnd,
        "title": title,
        "pid": pid.value,
    })
    return True



def enumerate_window_processes() -> list[dict]:
    """Return processes that have visible top-level windows.

    Returns list of {name, pid, title} sorted by process name.
    Processes are deduplicated — only the first (or main) window per
    process is included.
    """
    global _enum_windows_result
    _enum_windows_result = []
    callback = WNDENUMPROC(_window_enum_proc)
    _user32.EnumWindows(callback, 0)

    # Resolve process names and deduplicate
    seen_pids = set()
    result = []
    for w in _enum_windows_result:
        pid = w["pid"]
        if pid in seen_pids:
            continue
        seen_pids.add(pid)
        try:
            proc = psutil.Process(pid)
            name = proc.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            name = f"<pid:{pid}>"
        result.append({
            "name": name,
            "pid": pid,
            "title": w["title"],
        })

    result.sort(key=lambda r: r["name"].lower())
    return result


def enumerate_all_processes() -> list[dict]:
    """Return ALL running processes (including those without windows).

    Returns list of {name, pid} sorted by process name.
    """
    result = []
    seen_names = set()
    for proc in psutil.process_iter(["name", "pid"]):
        try:
            name = proc.info["name"]
            pid = proc.info["pid"]
            if name and name.lower() not in seen_names:
                seen_names.add(name.lower())
                result.append({"name": name, "pid": pid, "title": ""})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    result.sort(key=lambda r: r["name"].lower())
    return result


# ------------------------------------------------------------------
# Process List Dialog
# ------------------------------------------------------------------

# ── Pink theme colors ───────────────────────────────────────────────
_PBG_MAIN   = "#fff0f5"
_PBG_ACCENT = "#ffd6e0"
_PFG_DARK   = "#c44569"
_PFG_BODY   = "#6b3a4e"
_PHOT_PINK  = "#ff69b4"
_PWHITE     = "#ffffff"
_PLIGHT     = "#e89eb8"

import customtkinter as ctk


class ProcessListDialog(ctk.CTkToplevel):
    """Searchable, sortable process list with refresh button."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("♡ 选择应用进程")
        self.geometry("600x480")
        self.minsize(450, 320)
        self.resizable(True, True)
        self.grab_set()
        self.configure(fg_color=_PBG_MAIN)

        self._result: str | None = None
        self._all_processes: list[dict] = []
        self._filtered: list[dict] = []
        self._sort_key = "name"

        # Center
        self.update_idletasks()
        px, py = parent.winfo_x(), parent.winfo_y()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        self.geometry(f"+{px + (pw - 600) // 2}+{py + (ph - 480) // 2}")

        self._build_ui()
        self._refresh_processes(show_all=False)

        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Control-f>", lambda e: self._search_entry.focus_set())
        self._search_entry.focus_set()

    def get_result(self) -> str | None:
        return self._result

    def _build_ui(self):
        # --- Decorative top ---
        ctk.CTkLabel(self, text="❀  ✿   ♡   ❁   ✿   ♡   ❀",
                     text_color=_PLIGHT, font=("Segoe UI", 10),
                     ).pack(pady=(8, 0))
        ctk.CTkLabel(self, text="♡ 选择应用进程",
                     text_color=_PFG_DARK,
                     font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
                     ).pack(pady=(2, 8))

        # --- Search bar ---
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill=tk.X, padx=20, pady=(0, 4))

        ctk.CTkLabel(bar, text="🔍", text_color=_PFG_BODY,
                     font=("Segoe UI", 10)).pack(side=tk.LEFT)
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *a: self._apply_filter())
        self._search_entry = ctk.CTkEntry(
            bar, textvariable=self._search_var,
            font=("Segoe UI", 10), fg_color=_PWHITE, text_color=_PFG_BODY,
            border_color=_PBG_ACCENT, corner_radius=8,
        )
        self._search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8))

        self._show_all_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(bar, text="显示所有进程", variable=self._show_all_var,
                        command=self._on_show_all_toggle,
                        fg_color=_PHOT_PINK, hover_color=_PHOT_PINK,
                        text_color=_PFG_BODY, font=("Segoe UI", 10),
                        checkmark_color=_PWHITE, border_color=_PBG_ACCENT,
                        ).pack(side=tk.RIGHT)

        # --- Treeview ---
        tree_wrap = ctk.CTkFrame(self, fg_color=_PBG_ACCENT, corner_radius=10)
        tree_wrap.pack(fill=tk.BOTH, expand=True, padx=20, pady=8)
        tree_inner = ctk.CTkFrame(tree_wrap, fg_color=_PWHITE, corner_radius=8)
        tree_inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        columns = ("name", "pid", "title")
        self._tree = ttk.Treeview(
            tree_inner, columns=columns, show="headings", selectmode="browse",
        )
        self._tree.heading("name", text="进程名", command=lambda: self._sort_by("name"))
        self._tree.heading("pid", text="PID", command=lambda: self._sort_by("pid"))
        self._tree.heading("title", text="窗口标题", command=lambda: self._sort_by("title"))
        self._tree.column("name", width=220, minwidth=100)
        self._tree.column("pid", width=70, minwidth=50, anchor="center")
        self._tree.column("title", width=280, minwidth=100)

        # Treeview styling
        tv_style = ttk.Style(self)
        try:
            tv_style.theme_use("clam")
        except tk.TclError:
            pass
        tv_style.configure("Treeview", background=_PWHITE, foreground=_PFG_BODY,
                           fieldbackground=_PWHITE, borderwidth=0)
        tv_style.configure("Treeview.Heading", background=_PBG_ACCENT,
                           foreground=_PFG_DARK, font=("Segoe UI", 9, "bold"),
                           borderwidth=0)
        tv_style.map("Treeview", background=[("selected", _PBG_ACCENT)],
                     foreground=[("selected", _PFG_DARK)])

        scrollbar = ttk.Scrollbar(tree_inner, orient=tk.VERTICAL)
        scrollbar.config(command=self._tree.yview)
        self._tree.config(yscrollcommand=scrollbar.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 0), pady=4)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 2), pady=4)
        self._tree.bind("<Double-Button-1>", lambda e: self._on_select())

        # --- Buttons ---
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(pady=(0, 10), padx=20, fill=tk.X)

        ctk.CTkButton(btn_row, text="⟳ 刷新", command=self._on_refresh,
                      fg_color=_PBG_ACCENT, hover_color=_PHOT_PINK,
                      text_color=_PFG_DARK, corner_radius=13, width=70, height=28,
                      ).pack(side=tk.LEFT)
        ctk.CTkButton(btn_row, text="✎ 手动输入", command=self._on_manual,
                      fg_color=_PBG_ACCENT, hover_color=_PHOT_PINK,
                      text_color=_PFG_DARK, corner_radius=13, width=90, height=28,
                      ).pack(side=tk.LEFT, padx=8)
        ctk.CTkButton(btn_row, text="♡ 确认选择", command=self._on_select,
                      fg_color=_PBG_ACCENT, hover_color=_PHOT_PINK,
                      text_color=_PFG_DARK, corner_radius=13, width=90, height=28,
                      ).pack(side=tk.RIGHT)
        ctk.CTkButton(btn_row, text="取消", command=self.destroy,
                      fg_color=_PBG_ACCENT, hover_color=_PHOT_PINK,
                      text_color=_PFG_DARK, corner_radius=13, width=70, height=28,
                      ).pack(side=tk.RIGHT, padx=4)

        # --- Status ---
        self._status_var = tk.StringVar()
        ctk.CTkLabel(self, textvariable=self._status_var,
                     text_color=_PLIGHT, font=("Segoe UI", 9),
                     ).pack(side=tk.BOTTOM, anchor=tk.W, padx=20, pady=(0, 8))

    # ------------------------------------------------------------------
    # Logic
    # ------------------------------------------------------------------

    def _refresh_processes(self, show_all: bool = False):
        self._all_processes = (
            enumerate_all_processes() if show_all else enumerate_window_processes()
        )
        self._sort_by("name")

    def _sort_by(self, key: str):
        self._sort_key = key
        self._all_processes.sort(key=lambda r: (r.get(key, "") or "").lower())
        self._apply_filter()

    def _apply_filter(self):
        query = self._search_var.get().lower().strip()
        if query:
            self._filtered = [
                p for p in self._all_processes
                if query in p["name"].lower()
                or query in (p.get("title", "") or "").lower()
                or str(p["pid"]) == query
            ]
        else:
            self._filtered = list(self._all_processes)

        self._tree.delete(*self._tree.get_children())
        for p in self._filtered:
            self._tree.insert(
                "", tk.END,
                values=(p["name"], p["pid"], p.get("title", "") or ""),
            )
        self._status_var.set(
            f"显示 {len(self._filtered)} / {len(self._all_processes)} 个进程"
        )

    def _on_show_all_toggle(self):
        self._refresh_processes(show_all=self._show_all_var.get())

    def _on_refresh(self):
        self._refresh_processes(show_all=self._show_all_var.get())

    def _on_select(self):
        sel = self._tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在列表中选择一个进程。", parent=self)
            return
        values = self._tree.item(sel[0], "values")
        self._result = values[0]  # process name
        self.destroy()

    def _on_manual(self):
        dialog = ctk.CTkInputDialog(
            text="输入进程名（例如 notepad.exe）：",
            title="♡ 手动输入",
        )
        name = dialog.get_input()
        if name and name.strip():
            self._result = name.strip()
            self.destroy()


