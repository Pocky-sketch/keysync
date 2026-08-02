r"""
config.py — Thread-safe configuration management for KeySync.

Persists allowed-app list, key mappings, and enabled state to
%APPDATA%\KeySync\config.json. All reads/writes are protected by a
threading.Lock. Writes are atomic (write to .tmp, then os.replace).
"""

import json
import os
import threading


class Config:
    """Thread-safe configuration backed by a JSON file."""

    def __init__(self, path: str | None = None):
        if path is None:
            appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
            self._dir = os.path.join(appdata, "KeySync")
            path = os.path.join(self._dir, "config.json")
        else:
            self._dir = os.path.dirname(path)

        self._path = path
        self._lock = threading.Lock()

        self._data: dict = {
            "version": 4,
            "enabled": True,
            "toggle_hotkey": "pause",
            "typing_pause": True,
            "chat_keys_default": ["enter", "t", "/"],
            "app_chat_keys": {},  # {"VRChat.exe": ["enter"]}
            "key_mappings": [
                {"source": "w", "target": "left shift"},
            ],
            "allowed_apps": [],
            "warned_admin": False,
            "autoclick_enabled": False,
            "autoclick_interval": 50,  # ms between clicks while held
        }

        self._has_run_before = os.path.exists(self._path)
        self._load()

    # ------------------------------------------------------------------
    # Public API — general
    # ------------------------------------------------------------------

    def has_run_before(self) -> bool:
        """Return True if the config file existed at launch time."""
        return self._has_run_before

    def is_enabled(self) -> bool:
        with self._lock:
            return self._data["enabled"]

    def set_enabled(self, v: bool):
        with self._lock:
            self._data["enabled"] = v
            self._save()

    def toggle_enabled(self) -> bool:
        with self._lock:
            self._data["enabled"] = not self._data["enabled"]
            self._save()
            return self._data["enabled"]

    def admin_warning_shown(self) -> bool:
        with self._lock:
            return self._data.get("warned_admin", False)

    def mark_admin_warning_shown(self):
        with self._lock:
            self._data["warned_admin"] = True
            self._save()

    def get_toggle_hotkey(self) -> str:
        with self._lock:
            return self._data.get("toggle_hotkey", "pause")

    def set_toggle_hotkey(self, key: str):
        with self._lock:
            self._data["toggle_hotkey"] = key.lower().strip()
            self._save()

    def is_typing_pause(self) -> bool:
        with self._lock:
            return self._data.get("typing_pause", True)

    def set_typing_pause(self, v: bool):
        with self._lock:
            self._data["typing_pause"] = v
            self._save()

    def get_chat_keys(self, app_name: str) -> list[str]:
        """Return chat keys for a specific app, or the default set."""
        with self._lock:
            per_app = self._data.get("app_chat_keys", {})
            if app_name.lower() in (k.lower() for k in per_app):
                for k, v in per_app.items():
                    if k.lower() == app_name.lower():
                        return list(v)
            return list(self._data.get("chat_keys_default", ["enter", "t", "/"]))

    def has_app_chat_keys(self, app_name: str) -> bool:
        """Return True if this app has its own chat keys (not just defaults)."""
        with self._lock:
            per_app = self._data.get("app_chat_keys", {})
            return app_name.lower() in (k.lower() for k in per_app)

    def set_app_chat_keys(self, app_name: str, keys: list[str]):
        """Set per-app chat keys. Empty list means 'use default'."""
        with self._lock:
            per_app = self._data.setdefault("app_chat_keys", {})
            # Normalize — store with original casing from allowed_apps
            apps = self._data["allowed_apps"]
            real_name = app_name
            for a in apps:
                if a.lower() == app_name.lower():
                    real_name = a
                    break
            if keys:
                per_app[real_name] = [k.lower().strip() for k in keys if k.strip()]
            else:
                # Remove to fall back to default
                for k in list(per_app.keys()):
                    if k.lower() == app_name.lower():
                        del per_app[k]
                        break
            self._save()

    def get_default_chat_keys(self) -> list[str]:
        with self._lock:
            return list(self._data.get("chat_keys_default", ["enter", "t", "/"]))

    def set_default_chat_keys(self, keys: list[str]):
        with self._lock:
            self._data["chat_keys_default"] = [k.lower().strip() for k in keys if k.strip()]
            self._save()

    # ------------------------------------------------------------------
    # Public API — auto-clicker
    # ------------------------------------------------------------------

    def is_autoclick_enabled(self) -> bool:
        with self._lock:
            return bool(self._data.get("autoclick_enabled", False))

    def set_autoclick_enabled(self, v: bool):
        with self._lock:
            self._data["autoclick_enabled"] = bool(v)
            self._save()

    def get_autoclick_interval(self) -> int:
        """Return click interval in milliseconds (clamped to [20, 500])."""
        with self._lock:
            return max(20, min(500, int(self._data.get("autoclick_interval", 50))))

    def set_autoclick_interval(self, ms: int):
        with self._lock:
            self._data["autoclick_interval"] = max(20, min(500, int(ms)))
            self._save()

    # ------------------------------------------------------------------
    # Public API — key mappings
    # ------------------------------------------------------------------

    def get_mappings(self) -> list[dict]:
        """Return a *copy* of the key-mappings list.

        Each item is {"source": key_name, "target": key_name}.
        """
        with self._lock:
            return list(self._data["key_mappings"])

    def add_mapping(self, source: str, target: str):
        """Add a source→target key mapping (no duplicate source)."""
        source = source.lower().strip()
        target = target.lower().strip()
        if not source or not target:
            return
        with self._lock:
            # Replace if source already exists
            for m in self._data["key_mappings"]:
                if m["source"] == source:
                    m["target"] = target
                    self._save()
                    return
            self._data["key_mappings"].append({"source": source, "target": target})
            self._save()

    def remove_mapping(self, idx: int):
        """Remove a mapping by index."""
        with self._lock:
            if 0 <= idx < len(self._data["key_mappings"]):
                del self._data["key_mappings"][idx]
                self._save()

    def update_mapping(self, idx: int, source: str, target: str):
        """Update a mapping at the given index."""
        with self._lock:
            if 0 <= idx < len(self._data["key_mappings"]):
                self._data["key_mappings"][idx] = {
                    "source": source.lower().strip(),
                    "target": target.lower().strip(),
                }
                self._save()

    # ------------------------------------------------------------------
    # Public API — allowed apps
    # ------------------------------------------------------------------

    def get_allowed_apps(self) -> list[str]:
        with self._lock:
            return list(self._data["allowed_apps"])

    def add_app(self, name: str):
        name = name.strip()
        if not name:
            return
        with self._lock:
            lower_list = [a.lower() for a in self._data["allowed_apps"]]
            if name.lower() not in lower_list:
                self._data["allowed_apps"].append(name)
                self._save()

    def remove_app(self, name: str):
        with self._lock:
            lower_list = [a.lower() for a in self._data["allowed_apps"]]
            try:
                idx = lower_list.index(name.lower())
                del self._data["allowed_apps"][idx]
                self._save()
            except ValueError:
                pass

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self):
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self._data["version"] = loaded.get("version", 1)
                self._data["enabled"] = loaded.get("enabled", True)
                self._data["toggle_hotkey"] = loaded.get("toggle_hotkey", "pause")
                self._data["typing_pause"] = loaded.get("typing_pause", True)
                self._data["chat_keys_default"] = loaded.get("chat_keys_default", ["enter", "t", "/"])
                self._data["app_chat_keys"] = loaded.get("app_chat_keys", {})
                self._data["warned_admin"] = loaded.get("warned_admin", False)
                self._data["autoclick_enabled"] = loaded.get("autoclick_enabled", False)
                self._data["autoclick_interval"] = loaded.get("autoclick_interval", 50)

                # Migrate old format: source_key / target_key → key_mappings
                if "key_mappings" in loaded:
                    self._data["key_mappings"] = loaded["key_mappings"]
                elif "source_key" in loaded or "target_key" in loaded:
                    self._data["key_mappings"] = [{
                        "source": loaded.get("source_key", "w"),
                        "target": loaded.get("target_key", "left shift"),
                    }]
                # else keep default

                apps = loaded.get("allowed_apps", [])
                if isinstance(apps, list):
                    self._data["allowed_apps"] = apps

                # Persist only when a migration actually happened
                if loaded.get("version", 1) != 4:
                    self._data["version"] = 4
                    self._save()
        except FileNotFoundError:
            os.makedirs(self._dir, exist_ok=True)
            self._save()
        except (json.JSONDecodeError, OSError):
            pass

    def _save(self):
        os.makedirs(self._dir, exist_ok=True)
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self._path)
