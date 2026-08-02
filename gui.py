"""
gui.py — CustomTkinter configuration window for KeySync.

Modern flat/rounded UI with pink theme.
"""

import tkinter as tk
import customtkinter as ctk

from config import Config

# ── Colors ──────────────────────────────────────────────────────────
PINK_BG     = "#fff0f5"
PINK_FRAME  = "#ffe4ec"
PINK_BTN    = "#ffb6c1"
PINK_HOVER  = "#ff69b4"
PINK_BORDER = "#f8c8d8"
ROSE        = "#c44569"
PLUM        = "#6b3a4e"
LIGHT       = "#e89eb8"
WHITE       = "#ffffff"
DARK_BG     = "#2b1a22"  # for contrast if needed

# ── Decorative ─────────────────────────────────────────────────────
LACE_TOP = "❀  ✿   ♡   ❁   ✿   ♡   ❀"
LACE_BOT = "┈┈┈┈┈ ♡ ໒꒰ྀིっ˕ -｡꒱ྀི১ ♡ ┈┈┈┈┈"
HEART    = "♡"

# ── tkinter → keyboard key name mapping ────────────────────────────
_TK_TO_KB = {
    "Shift_L": "left shift", "Shift_R": "right shift",
    "Control_L": "left ctrl", "Control_R": "right ctrl",
    "Alt_L": "left alt", "Alt_R": "right alt",
    "space": "space", "Return": "enter", "Tab": "tab",
    "Escape": "esc", "BackSpace": "backspace", "Delete": "delete",
    "Insert": "insert", "Home": "home", "End": "end",
    "Prior": "page up", "Next": "page down",
    "Up": "up", "Down": "down", "Left": "left", "Right": "right",
    "Caps_Lock": "caps lock", "Num_Lock": "num lock",
    "Scroll_Lock": "scroll lock", "Print": "print screen",
    "Pause": "pause", "Menu": "menu",
}

def tk_key_to_name(event) -> str | None:
    keysym = event.keysym
    if keysym in _TK_TO_KB:
        return _TK_TO_KB[keysym]
    if keysym.startswith("F") and len(keysym) <= 3:
        return keysym.lower()
    if len(keysym) == 1:
        return keysym.lower()
    return keysym.lower()


# ── ConfigGUI ──────────────────────────────────────────────────────

class ConfigGUI:
    def __init__(self, config: Config):
        self._config = config
        self._visible = False
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")

        self._root = ctk.CTk()
        self._root.title(f"{HEART} 按键同步 {HEART}")
        self._root.geometry("520x500")
        self._root.minsize(440, 400)
        self._root.configure(fg_color=PINK_BG)
        self._root.protocol("WM_DELETE_WINDOW", self.quit)

        try:
            __import__("ctypes").windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

        self._build_ui()
        self._refresh_list()

    # ── Public API ──────────────────────────────────────────────────

    def is_visible(self):
        return self._visible

    def show(self):
        self._root.deiconify()
        self._root.lift()
        self._root.focus_force()
        self._visible = True
        self._refresh_list()

    def hide(self):
        self._root.withdraw()
        self._visible = False

    def run(self):
        self._root.mainloop()

    def get_hwnd(self) -> int:
        """Return the real top-level HWND of the config window.

        Used to post WM_CLOSE from another thread (tkinter is not
        thread-safe, so the tray thread must not call root.quit()
        directly).

        NOTE: winfo_id() returns Tk's internal wrapper handle, not the
        top-level window — WM_CLOSE posted to it never fires the
        WM_DELETE_WINDOW protocol. FindWindowW by title finds the real
        top-level window.
        """
        import ctypes
        import ctypes.wintypes as wintypes
        user32 = ctypes.windll.user32
        user32.FindWindowW.restype = wintypes.HWND
        user32.FindWindowW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPCWSTR,
        ]
        title = self._root.title()
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            return int(hwnd)
        return int(self._root.winfo_id())  # fallback

    def quit(self):
        self._root.quit()
        self._root.destroy()

    # ── UI ──────────────────────────────────────────────────────────

    def _build_ui(self):
        # --- Lace top ---
        ctk.CTkLabel(self._root, text=LACE_TOP,
                     text_color=LIGHT, font=("Segoe UI", 10)).pack(pady=(8, 0))

        # --- Title ---
        ctk.CTkLabel(self._root,
                     text=f"{HEART}  按键同步  {HEART}",
                     text_color=ROSE,
                     font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
                     ).pack(pady=(6, 2))

        ctk.CTkLabel(self._root,
                     text="仅当焦点窗口属于以下应用时，按键映射才会生效",
                     text_color=PLUM, font=("Segoe UI", 10),
                     ).pack(pady=(0, 8))

        # --- Separator ---
        ctk.CTkFrame(self._root, height=1, fg_color=PINK_BORDER,
                     corner_radius=0).pack(fill=tk.X, padx=60)

        # --- Key mapping list ---
        map_outer, self._mapping_listbox = self._make_listbox(height=3)
        map_outer.pack(fill=tk.X, padx=28, pady=(12, 4))
        self._mapping_listbox.bind("<Double-Button-1>", lambda e: self._on_edit_mapping())

        map_btn_row = ctk.CTkFrame(self._root, fg_color="transparent")
        map_btn_row.pack(fill=tk.X, padx=28)
        ctk.CTkLabel(map_btn_row, text="  按键映射",
                     text_color=ROSE, font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
        ctk.CTkButton(map_btn_row, text=f"{HEART} 添加", width=70, height=26,
                      fg_color=PINK_BTN, hover_color=PINK_HOVER,
                      text_color=ROSE, corner_radius=13,
                      command=self._on_add_mapping).pack(side=tk.RIGHT, padx=2)
        ctk.CTkButton(map_btn_row, text="✕ 移除", width=70, height=26,
                      fg_color=PINK_BTN, hover_color=PINK_HOVER,
                      text_color=ROSE, corner_radius=13,
                      command=self._on_remove_mapping).pack(side=tk.RIGHT, padx=2)

        # --- App list ---
        ctk.CTkFrame(self._root, height=1, fg_color=PINK_BORDER,
                     corner_radius=0).pack(fill=tk.X, padx=60, pady=(12, 4))

        app_outer, self._app_listbox = self._make_listbox(height=8)
        app_outer.pack(fill=tk.BOTH, expand=True, padx=28, pady=(4, 4))
        self._app_listbox.bind("<Double-Button-1>", lambda e: self._on_remove())

        app_btn_row = ctk.CTkFrame(self._root, fg_color="transparent")
        app_btn_row.pack(fill=tk.X, padx=28, pady=(0, 4))
        ctk.CTkLabel(app_btn_row, text="  同步应用",
                     text_color=ROSE, font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
        for text, cmd in [
            ("📋 浏览", self._on_browse),
            ("⌨ 手动", self._on_manual),
            ("💬 聊天键", self._on_edit_chat_keys),
            ("✕ 移除", self._on_remove),
        ]:
            ctk.CTkButton(app_btn_row, text=text, width=62, height=26,
                          fg_color=PINK_BTN, hover_color=PINK_HOVER,
                          text_color=ROSE, corner_radius=13,
                          command=cmd).pack(side=tk.RIGHT, padx=2)

        # --- Enable toggle ---
        self._enabled_var = tk.BooleanVar(value=self._config.is_enabled())
        ctk.CTkCheckBox(self._root,
                        text="✧ 启用同步 (Pause 键切换)",
                        variable=self._enabled_var,
                        command=self._on_toggle_enabled,
                        fg_color=PINK_HOVER, hover_color=PINK_HOVER,
                        text_color=PLUM, font=("Segoe UI", 10),
                        checkmark_color=WHITE, border_color=PINK_BORDER,
                        ).pack(pady=(12, 2))

        # --- Typing-pause toggle ---
        self._typing_pause_var = tk.BooleanVar(value=self._config.is_typing_pause())
        ctk.CTkCheckBox(self._root,
                        text="✎ 输入时暂停同步（聊天键自动暂停）",
                        variable=self._typing_pause_var,
                        command=self._on_toggle_typing_pause,
                        fg_color=PINK_HOVER, hover_color=PINK_HOVER,
                        text_color=PLUM, font=("Segoe UI", 10),
                        checkmark_color=WHITE, border_color=PINK_BORDER,
                        ).pack(pady=(0, 4))

        # --- Auto-click toggle + interval ---
        auto_row = ctk.CTkFrame(self._root, fg_color="transparent")
        auto_row.pack(pady=(0, 4))
        self._autoclick_var = tk.BooleanVar(value=self._config.is_autoclick_enabled())
        ctk.CTkCheckBox(auto_row,
                        text="🖱 按住左键自动连点（松开停止）",
                        variable=self._autoclick_var,
                        command=self._on_toggle_autoclick,
                        fg_color=PINK_HOVER, hover_color=PINK_HOVER,
                        text_color=PLUM, font=("Segoe UI", 10),
                        checkmark_color=WHITE, border_color=PINK_BORDER,
                        ).pack(side=tk.LEFT)
        ctk.CTkButton(auto_row, text="间隔", width=56, height=24,
                      fg_color=PINK_BTN, hover_color=PINK_HOVER,
                      text_color=ROSE, corner_radius=12,
                      command=self._on_set_autoclick_interval
                      ).pack(side=tk.RIGHT, padx=2)

        # --- Lace bottom ---
        ctk.CTkLabel(self._root, text=LACE_BOT,
                     text_color=LIGHT, font=("Segoe UI", 10)).pack(pady=(2, 2))

        # --- Status ---
        ctk.CTkLabel(self._root,
                     text=f"{HEART}  按键同步 v1.1  {HEART}",
                     text_color=LIGHT, font=("Segoe UI", 9),
                     ).pack(side=tk.BOTTOM, pady=(0, 8))

    # ── Listbox helper ──────────────────────────────────────────────

    def _make_listbox(self, height=5):
        """Return (outer_frame, listbox) tuple. Pack the frame."""
        outer = ctk.CTkFrame(self._root, fg_color=PINK_BORDER, corner_radius=10)
        inner = ctk.CTkFrame(outer, fg_color=WHITE, corner_radius=8)
        lb = tk.Listbox(inner, font=("Consolas", 10), height=height,
                        bg=WHITE, fg=PLUM, activestyle="none",
                        selectbackground=PINK_BTN, selectforeground=ROSE,
                        highlightthickness=0, borderwidth=0, relief="flat")
        inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        lb.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        return outer, lb

    # ── Handlers ────────────────────────────────────────────────────

    def _on_browse(self):
        from process_picker import ProcessListDialog
        dlg = ProcessListDialog(self._root)
        self._root.wait_window(dlg)
        name = dlg.get_result()
        if name:
            self._config.add_app(name)
            self._refresh_list()
            self._flash_listbox()

    def _on_manual(self):
        dialog = ctk.CTkInputDialog(
            text="输入进程名（例如 notepad.exe）：",
            title=f"{HEART} 手动输入",
        )
        name = dialog.get_input()
        if name and name.strip():
            self._config.add_app(name.strip())
            self._refresh_list()

    def _on_remove(self):
        sel = self._app_listbox.curselection()
        if not sel:
            from tkinter import messagebox
            messagebox.showinfo("提示", "请先选择要移除的应用。")
            return
        name = self._get_selected_app_name()
        from tkinter import messagebox
        ok = messagebox.askyesno("确认移除", f"确定要将 '{name}' 从列表中移除吗？")
        if ok:
            self._config.remove_app(name)
            self._refresh_list()

    def _on_edit_chat_keys(self):
        app_name = self._get_selected_app_name()
        if not app_name:
            from tkinter import messagebox
            messagebox.showinfo("提示", "请先在应用列表中选择一个要配置的应用。")
            return
        current = self._config.get_chat_keys(app_name)
        result = _chat_keys_dialog(self._root, app_name, current)
        if result is not None:
            self._config.set_app_chat_keys(app_name, result)
            self._refresh_list()
            self._flash_listbox()

    def _on_toggle_enabled(self):
        self._config.set_enabled(self._enabled_var.get())

    def _on_toggle_typing_pause(self):
        self._config.set_typing_pause(self._typing_pause_var.get())

    def _on_toggle_autoclick(self):
        self._config.set_autoclick_enabled(self._autoclick_var.get())

    def _on_set_autoclick_interval(self):
        current = self._config.get_autoclick_interval()
        dialog = ctk.CTkInputDialog(
            text=f"连点间隔（毫秒，20~500）：\n当前 {current} ms\n数值越小点击越快",
            title=f"{HEART} 连点间隔",
        )
        val = dialog.get_input()
        if val:
            try:
                self._config.set_autoclick_interval(int(val.strip()))
            except ValueError:
                from tkinter import messagebox
                messagebox.showinfo("提示", "请输入数字（毫秒）。")

    # ── Key mapping handlers ────────────────────────────────────────

    def _on_add_mapping(self):
        self._add_source = None
        self._add_target = None
        self._record_key_step(1)

    def _on_edit_mapping(self):
        sel = self._mapping_listbox.curselection()
        if not sel:
            return
        mappings = self._config.get_mappings()
        idx = sel[0]
        if idx >= len(mappings):
            return
        m = mappings[idx]
        self._add_source = m["source"]
        self._edit_idx = idx
        self._record_key_step(2)

    def _on_remove_mapping(self):
        sel = self._mapping_listbox.curselection()
        if not sel:
            from tkinter import messagebox
            messagebox.showinfo("提示", "请先选择要移除的映射。")
            return
        self._config.remove_mapping(sel[0])
        self._refresh_list()

    def _record_key_step(self, step: int):
        dlg = ctk.CTkToplevel(self._root)
        dlg.title("♡ 按键录制")
        dlg.geometry("340x190")
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.configure(fg_color=PINK_BG)

        dlg.update_idletasks()
        px, py = self._root.winfo_x(), self._root.winfo_y()
        pw, ph = self._root.winfo_width(), self._root.winfo_height()
        dlg.geometry(f"+{px + (pw - 340) // 2}+{py + (ph - 190) // 2}")

        if step == 1:
            text = "第 1 步：按下源键\n（即你要按的实际按键）"
        else:
            existing = self._add_source or "?"
            text = f"第 2 步：按下同步键\n源键 {existing} 按下时 → 自动同步此键"

        ctk.CTkLabel(dlg, text=text, text_color=ROSE,
                     font=("Segoe UI", 12, "bold"), justify="center",
                     ).pack(pady=(20, 8))
        ctk.CTkLabel(dlg, text="按 Esc 取消", text_color=LIGHT,
                     font=("Segoe UI", 9)).pack()

        def on_key(event):
            name = tk_key_to_name(event)
            if name is None:
                return
            if step == 1:
                self._add_source = name
                dlg.destroy()
                self._record_key_step(2)
            else:
                self._add_target = name
                dlg.destroy()
                self._finish_mapping()

        dlg.bind("<KeyPress>", on_key)
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        dlg.focus_set()

    def _finish_mapping(self):
        if self._add_source and self._add_target:
            if hasattr(self, '_edit_idx'):
                self._config.update_mapping(
                    self._edit_idx, self._add_source, self._add_target)
                del self._edit_idx
            else:
                self._config.add_mapping(self._add_source, self._add_target)
            self._refresh_list()
            self._flash_listbox()

    # ── Helpers ─────────────────────────────────────────────────────

    def _refresh_list(self):
        # Mapping list
        self._mapping_listbox.delete(0, tk.END)
        for m in self._config.get_mappings():
            self._mapping_listbox.insert(tk.END, f"  {m['source']:12s} →  {m['target']}")

        # App list
        self._app_listbox.delete(0, tk.END)
        apps = sorted(self._config.get_allowed_apps(), key=str.lower)
        if apps:
            max_len = max(len(a) for a in apps)
            default_str = ", ".join(self._config.get_default_chat_keys())
            for name in apps:
                keys = self._config.get_chat_keys(name)
                if self._config.has_app_chat_keys(name):
                    key_str = ", ".join(keys)
                else:
                    key_str = f"{default_str} (默认)"
                self._app_listbox.insert(tk.END,
                    f"  {name:<{max_len + 2}} 💬 {key_str}")

        self._enabled_var.set(self._config.is_enabled())
        self._typing_pause_var.set(self._config.is_typing_pause())
        self._autoclick_var.set(self._config.is_autoclick_enabled())

    def _get_selected_app_name(self):
        sel = self._app_listbox.curselection()
        if not sel:
            return None
        line = self._app_listbox.get(sel[0])
        idx = line.find("💬")
        if idx > 0:
            return line[:idx].strip()
        return line.strip()

    def _flash_listbox(self):
        self._app_listbox.config(bg=PINK_BTN)
        self._app_listbox.after(300, lambda: self._app_listbox.config(bg=WHITE))


# ── Dialogs ─────────────────────────────────────────────────────────

def _chat_keys_dialog(parent, app_name, current_keys):
    dlg = ctk.CTkToplevel(parent)
    dlg.title(f"💬 {app_name} — 聊天触发键")
    dlg.geometry("380x320")
    dlg.resizable(False, False)
    dlg.grab_set()
    dlg.configure(fg_color=PINK_BG)

    dlg.update_idletasks()
    px, py = parent.winfo_x(), parent.winfo_y()
    pw, ph = parent.winfo_width(), parent.winfo_height()
    dlg.geometry(f"+{px + (pw - 380) // 2}+{py + (ph - 320) // 2}")

    result = None

    ctk.CTkLabel(dlg, text=f"为 {app_name} 设置聊天触发键",
                 text_color=ROSE, font=("Segoe UI", 11, "bold"),
                 ).pack(pady=(16, 4))
    ctk.CTkLabel(dlg,
                 text="按这些键时会自动暂停按键同步\n每行一个，例如: enter, t, /, y",
                 text_color=LIGHT, font=("Segoe UI", 9), justify="center",
                 ).pack(pady=(0, 8))

    text = ctk.CTkTextbox(dlg, font=("Consolas", 11),
                          fg_color=WHITE, text_color=PLUM,
                          border_color=PINK_BORDER, border_width=1,
                          corner_radius=8, height=140)
    text.insert("1.0", "\n".join(current_keys))
    text.pack(padx=20, pady=(0, 8))

    btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
    btn_row.pack()

    def on_save():
        nonlocal result
        content = text.get("1.0", "end-1c").strip()
        result = [k.strip().lower() for k in content.split("\n") if k.strip()]
        dlg.destroy()

    ctk.CTkButton(btn_row, text="♡ 保存", command=on_save,
                  fg_color=PINK_BTN, hover_color=PINK_HOVER,
                  text_color=ROSE, corner_radius=13,
                  ).pack(side=tk.LEFT, padx=4)
    ctk.CTkButton(btn_row, text="取消", command=dlg.destroy,
                  fg_color=PINK_BTN, hover_color=PINK_HOVER,
                  text_color=ROSE, corner_radius=13,
                  ).pack(side=tk.LEFT, padx=4)

    dlg.bind("<Escape>", lambda e: dlg.destroy())
    text.focus_set()
    parent.wait_window(dlg)
    return result
