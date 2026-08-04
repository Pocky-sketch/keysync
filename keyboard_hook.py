"""
keyboard_hook.py — Global keyboard hook + key injection for 按键同步.

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

# Inject-suppression state: when the user presses a chord key (e.g. Tab)
# while we hold an injected key, we release the injected key and
# remember it here; when the user releases the chord key, we re-press.
_pending_reinject: dict | None = None  # {source: target} to re-press
_suppress_key: str | None = None       # the key that triggered suppression

# Re-send guard: after suppressing a physical chord key we re-send a
# clean copy (keyboard.press). That re-sent event passes through the
# hook again — without a guard it would re-trigger suppression, loop
# forever, and block the entire keyboard (and leak modifiers that then
# fire OS shortcuts like Win+C → m365.cloud.microsoft).
_resend_key: str | None = None
_resend_until: float = 0.0

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
    """Hook callback wrapper — the real logic lives in _on_key_event_impl.

    CRITICAL: this is a BLOCKING hook (suppress=True). keyboard 0.13.5
    does `if not all(hook(e) for hook in blocking_hooks): return False`,
    so the callback MUST return a truthy value to let an event through —
    a bare `return` (None) or an exception swallows the key and blocks
    the whole keyboard. Only the suppression branch returns False
    (intentional swallow); every other path returns True.
    """
    try:
        return _on_key_event_impl(event)
    except Exception:
        return True  # fail-open: pass the event through (never None!)


def _on_key_event_impl(event: keyboard.KeyboardEvent):
    global _keys_down, _chat_paused_until, _pending_reinject, _suppress_key, _resend_key, _resend_until

    if _config is None:
        return True

    if not _config.is_enabled():
        return True

    # Events we injected ourselves (re-inject / re-send) must pass
    # through untouched — otherwise we'd loop or swallow our own keys.
    if getattr(event, "is_injected", False):
        return True

    event_name = event.name.lower()
    is_down = event.event_type == keyboard.KEY_DOWN

    # Our own clean re-send (from the suppression guard below): consume
    # the guard and pass the event through. Without this, the re-sent
    # chord key would re-trigger suppression → infinite loop → whole
    # keyboard blocks.
    if (is_down and _resend_key is not None
            and event_name == _resend_key and time.time() < _resend_until):
        _resend_key = None
        return True

    mappings = _config.get_mappings()
    if not mappings:
        return True

    # --- Re-inject after suppression: the chord key (e.g. Tab) is up,
    # so restore the injected keys we released. Only restore sources
    # that are STILL physically held — if the user released them while
    # the chord key was down, restoring would leak a stuck modifier.
    if not is_down and _pending_reinject and event_name == _suppress_key:
        saved = _pending_reinject
        _pending_reinject = None
        _suppress_key = None
        for source, target in saved.items():
            try:
                still_held = bool(keyboard.is_pressed(source))
            except Exception:
                still_held = False
            if still_held:
                _keys_down[source] = True
                if (_foreground_monitor is not None
                        and _foreground_monitor.is_allowed()):
                    try:
                        keyboard.press(target)
                    except Exception:
                        pass
            else:
                _keys_down[source] = False
        return True

    # --- Chat-key detection (only when typing-pause is enabled) ---
    if _config.is_typing_pause() and is_down:
        fg_name = _foreground_monitor.get_foreground_name() if _foreground_monitor else ""
        chat_keys = set(_config.get_chat_keys(fg_name)) if fg_name else set()

        # Pressing a chat key while paused → resume (e.g. Enter sends chat)
        if _chat_paused_until > 0 and event_name in chat_keys:
            _chat_paused_until = 0.0
            return True  # don't process this key as a mapping

        # Pressing a chat key while not paused → pause sync
        if _chat_paused_until == 0.0 and chat_keys and event_name in chat_keys:
            _chat_paused_until = time.time() + _CHAT_TIMEOUT
            return True  # don't process this key as a mapping

        # While paused (and still within timeout), any key extends it —
        # typing keeps the pause alive. Expired pauses are cleared by
        # is_chat_paused() so the pause can't be re-armed forever.
        if is_chat_paused():
            _chat_paused_until = time.time() + _CHAT_TIMEOUT

    # --- Inject-in-progress guard: suppress OS shortcuts like Shift+Tab
    # (Steam overlay) while we hold a mapped target key.
    # Releasing the injected key alone is NOT enough: the physical
    # chord-key event (Tab down) is already in the input queue ahead of
    # our injected release, so Steam still sees Shift+Tab. We must
    # SWALLOW the physical chord-key down and re-send a clean copy —
    # by then the injected modifiers are released, and the re-sent
    # event (is_injected=True) passes through untouched.
    # Runs AFTER chat-key detection so chat keys (e.g. Enter) are never
    # swallowed by this guard, and is skipped while chat-paused.
    if is_down and _keys_down and not is_chat_paused():
        # Only keys that are ACTUALLY injected count — checking dict
        # membership (not value) kept stale {"w": False} entries alive,
        # so the re-sent chord key re-triggered suppression forever.
        injected = {m["target"] for m in mappings if _keys_down.get(m["source"])}
        suppress_after = {
            "tab", "esc", "space", "enter", "f1", "f2", "f3", "f4", "f5",
            "f6", "f7", "f8", "f9", "f10", "f11", "f12",
            "left", "right", "up", "down",
            "alt", "left alt", "right alt", "ctrl", "left ctrl", "right ctrl",
            "left windows", "right windows", "win", "menu",
        }
        if event_name in suppress_after and injected:
            # 1. Remember which injected keys to restore on chord-key up.
            _pending_reinject = {
                m["source"]: m["target"]
                for m in mappings if _keys_down.get(m["source"])
            }
            _suppress_key = event_name
            # 2. Release injected keys and REMOVE their state entirely —
            #    an empty _keys_down guarantees the re-sent event below
            #    cannot re-enter this block (loop prevention).
            for m in mappings:
                if _keys_down.get(m["source"]):
                    try:
                        keyboard.release(m["target"])
                    except Exception:
                        pass
                    del _keys_down[m["source"]]
            # 3. Swallow the physical chord-key down and re-send a clean
            #    copy (no modifier held anymore). Guard the re-send so it
            #    passes through the hook instead of being suppressed again.
            _resend_key = event_name
            _resend_until = time.time() + 0.25
            try:
                keyboard.press(event_name)
            except Exception:
                pass
            return False  # block the physical event

    # --- Process mappings ---
    source_to_target = {m["source"]: m["target"] for m in mappings}

    if event_name not in source_to_target:
        return True

    target_key = source_to_target[event_name]

    if event.event_type == keyboard.KEY_DOWN:
        # Chat-pause gate: while paused (within timeout), don't inject.
        if is_chat_paused():
            return True
        if not _keys_down.get(event_name, False):
            _keys_down[event_name] = True
            if _foreground_monitor is not None and _foreground_monitor.is_allowed():
                keyboard.press(target_key)
        return True  # ALWAYS truthy — blocking hook swallows falsy returns

    elif event.event_type == keyboard.KEY_UP:
        # If this source key is in the suppression restore list, the user
        # released it before the chord key — drop it so we never restore
        # its injection (avoids a stuck modifier on future keys).
        if _pending_reinject and event_name in _pending_reinject:
            del _pending_reinject[event_name]
        if _keys_down.get(event_name, False):
            _keys_down[event_name] = False
            # Always release keys we injected, even if chat-paused or the
            # foreground app changed — otherwise the target key stays stuck.
            keyboard.release(target_key)
        return True  # ALWAYS truthy — blocking hook swallows falsy returns


# ------------------------------------------------------------------
# Toggle hotkey
# ------------------------------------------------------------------

def _on_toggle():
    global _keys_down, _pending_reinject, _suppress_key, _resend_key
    if _config is not None:
        new_state = _config.toggle_enabled()
        if not new_state:
            _pending_reinject = None
            _suppress_key = None
            _resend_key = None
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

        self._hook_handler = keyboard.hook(_on_key_event, suppress=True)

        try:
            hotkey = _config.get_toggle_hotkey()
            if hotkey:
                _toggle_hotkey_handler = keyboard.add_hotkey(
                    hotkey, _on_toggle, suppress=False
                )
        except Exception:
            pass

    def stop(self):
        global _toggle_hotkey_handler, _chat_paused_until, _pending_reinject, _suppress_key, _resend_key
        self._running = False
        _pending_reinject = None
        _suppress_key = None
        _resend_key = None

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
