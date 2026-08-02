"""
keyboard_hook.py — Global keyboard hook + key injection for KeySync.

- Multiple source→target key mappings
- Chat-key pause: per-app chat keys trigger pause; any key extends timeout;
  pressing Enter while paused resumes immediately.
- Toggle hotkey: default Pause key to manually switch sync on/off.
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

# Toggle hotkey
_toggle_hotkey_handler = None


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
    global _keys_down, _chat_paused_until

    if _config is None:
        return

    if not _config.is_enabled():
        return

    mappings = _config.get_mappings()
    if not mappings:
        return

    event_name = event.name.lower()

    # --- Chat-key detection (only when typing-pause is enabled) ---
    if _config.is_typing_pause() and event.event_type == keyboard.KEY_DOWN:
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
        global _toggle_hotkey_handler
        if self._running:
            return
        self._running = True

        self._hook_handler = keyboard.hook(_on_key_event, suppress=False)

        try:
            hotkey = _config.get_toggle_hotkey()
            if hotkey:
                _toggle_hotkey_handler = keyboard.add_hotkey(
                    hotkey, _on_toggle, suppress=False
                )
        except Exception:
            pass

    def stop(self):
        global _toggle_hotkey_handler, _chat_paused_until
        self._running = False

        if _toggle_hotkey_handler is not None:
            try:
                keyboard.remove_hotkey(_toggle_hotkey_handler)
            except Exception:
                pass
            _toggle_hotkey_handler = None

        if self._hook_handler is not None:
            keyboard.unhook(self._hook_handler)
            self._hook_handler = None

        _chat_paused_until = 0.0
