"""
keyboard_hook.py — Global keyboard hook + key injection for 按键同步.

- Multiple source→target key mappings
- Chat-key pause: per-app chat keys trigger pause; any key extends timeout;
  pressing Enter while paused resumes immediately.
- Toggle hotkey: default Pause key to manually switch sync on/off.

SAFETY DESIGN (2026-08):
- Non-blocking hook (suppress=False). The callback NEVER swallows keys
  and never re-sends them — earlier attempts to suppress Shift+Tab by
  swallowing + re-injecting caused a blocking-hook return-value bug that
  froze the entire keyboard. Steam's overlay is detected via raw input /
  in-game injection anyway, so suppressing at the low-level hook can't
  stop it. Dropping the suppression logic entirely keeps the keyboard
  unconditionally safe.
- KEY_UP always releases injected keys (even while chat-paused or after
  the foreground app changed) — prevents stuck keys.
"""

import time

import keyboard

from foreground_monitor import ForegroundMonitor
from config import Config

# ------------------------------------------------------------------
# Module-level state
# ------------------------------------------------------------------
_foreground_monitor: ForegroundMonitor | None = None
_config: Config | None = None
_keys_down: dict[str, bool] = {}

# Chat-mode state
_chat_paused_until: float = 0.0
_CHAT_TIMEOUT = 15.0  # auto-resume after idle

# Optional callback invoked after _on_toggle changes the enabled state.
# Set by main.py to the GUI's thread-safe notifier so the UI stays in
# sync when the Pause hotkey toggles sync.
_config_change_cb = None


# ------------------------------------------------------------------
# Chat-pause state
# ------------------------------------------------------------------

def is_chat_paused() -> bool:
    """Return True if chat-pause is active (and its timeout hasn't expired).

    Expired pauses are cleared here so callers don't each re-implement
    the timeout check. Used by the keyboard hook and the auto-clicker.
    """
    global _chat_paused_until
    if _chat_paused_until <= 0:
        return False
    if time.time() >= _chat_paused_until:
        _chat_paused_until = 0.0  # timeout expired — resume
        return False
    return True


# ------------------------------------------------------------------
# Hook callback
# ------------------------------------------------------------------

def _on_key_event(event: keyboard.KeyboardEvent):
    """Handle a key event. Never swallows keys (non-blocking hook).

    Note: keyboard.KeyboardEvent has no is_injected attribute in 0.13.5,
    so we cannot distinguish our own injected events — but since we
    never block events, that doesn't matter.
    """
    global _keys_down, _chat_paused_until

    if _config is None:
        return

    event_name = event.name.lower()
    is_down = event.event_type == keyboard.KEY_DOWN

    # --- Hotkeys (matched here, BEFORE the enabled gate) ---
    # keyboard.add_hotkey is broken on this machine: its callback never
    # fires even though the library's low-level hook itself works
    # (verified 2026-08-11 with SendInput probes). So both toggles are
    # matched directly from the raw hook stream. Doing this before the
    # enabled gate guarantees a hotkey can always re-enable what it
    # disabled.
    if is_down:
        try:
            toggle_hk = _config.get_toggle_hotkey().lower()
            if toggle_hk and event_name == toggle_hk:
                _on_toggle()
        except Exception:
            pass
        try:
            ac_hk = _config.get_autoclick_hotkey().lower()
            if ac_hk and not ac_hk.startswith("mouse") and event_name == ac_hk:
                _on_autoclick_toggle()
        except Exception:
            pass

    if not _config.is_enabled():
        return

    mappings = _config.get_mappings()
    if not mappings:
        return

    # --- Chat-key detection (only when typing-pause is enabled) ---
    if _config.is_typing_pause() and is_down:
        fg_name = _foreground_monitor.get_foreground_name() if _foreground_monitor else ""
        chat_keys = set(_config.get_chat_keys(fg_name)) if fg_name else set()

        # Pressing a chat key while paused → resume (e.g. Enter sends chat)
        if _chat_paused_until > 0 and event_name in chat_keys:
            _chat_paused_until = 0.0
            return  # don't process this key as a mapping

        # Pressing a chat key while not paused → pause sync
        if _chat_paused_until == 0.0 and chat_keys and event_name in chat_keys:
            _chat_paused_until = time.time() + _CHAT_TIMEOUT
            return  # don't process this key as a mapping

        # While paused (and still within timeout), any key extends it —
        # typing keeps the pause alive. Expired pauses are cleared by
        # is_chat_paused() so the pause can't be re-armed forever.
        if is_chat_paused():
            _chat_paused_until = time.time() + _CHAT_TIMEOUT

    # --- Process mappings ---
    source_to_target = {m["source"]: m["target"] for m in mappings}

    if event_name not in source_to_target:
        return

    target_key = source_to_target[event_name]

    if event.event_type == keyboard.KEY_DOWN:
        # Chat-pause gate: while paused (within timeout), don't inject.
        if is_chat_paused():
            return
        if not _keys_down.get(event_name, False):
            _keys_down[event_name] = True
            if _foreground_monitor is not None and _foreground_monitor.is_allowed():
                keyboard.press(target_key)

    elif event.event_type == keyboard.KEY_UP:
        if _keys_down.get(event_name, False):
            _keys_down[event_name] = False
            # Always release keys we injected, even if chat-paused or the
            # foreground app changed — otherwise the target key stays stuck.
            keyboard.release(target_key)


# ------------------------------------------------------------------
# Toggle hotkey
# ------------------------------------------------------------------

def _on_toggle():
    global _keys_down
    if _config is not None:
        new_state = _config.toggle_enabled()
        if not new_state:
            # Sync disabled — release every key we may have injected,
            # otherwise the target key stays stuck until re-enabled.
            for m in _config.get_mappings():
                try:
                    keyboard.release(m["target"])
                except Exception:
                    pass
            _keys_down = {}
        if _config_change_cb:
            try:
                _config_change_cb()
            except Exception:
                pass


def _on_autoclick_toggle():
    """Toggle the auto-clicker from its keyboard hotkey.

    Delayed import avoids a circular dependency (autoclicker imports
    keyboard_hook for is_chat_paused). By the time a hotkey fires, both
    modules are fully loaded.
    """
    from autoclicker import toggle_from_hotkey
    toggle_from_hotkey()


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


class KeyboardHook:
    """Manages the global keyboard hook and toggle hotkey."""

    def __init__(self, monitor: ForegroundMonitor, config: Config):
        global _foreground_monitor, _config
        _foreground_monitor = monitor
        _config = config

        self._hook_handler = None
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True

        # Non-blocking hook: the callback never swallows keys, so a bug
        # can never freeze the user's keyboard. Both toggle hotkeys are
        # matched inside _on_key_event (add_hotkey is unreliable here).
        self._hook_handler = keyboard.hook(_on_key_event, suppress=False)

    def refresh_autoclick_hotkey(self):
        """No-op (kept for the GUI's on_hotkey_changed wiring).

        The auto-click keyboard hotkey is matched from the raw hook
        stream on every key press, reading config live — no registration
        to refresh. Mouse side buttons are handled by the AutoClicker's
        own mouse hook.
        """

    def stop(self):
        global _chat_paused_until
        self._running = False

        if self._hook_handler is not None:
            keyboard.unhook(self._hook_handler)
            self._hook_handler = None

        _chat_paused_until = 0.0
