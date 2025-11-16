from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict

import requests

APP_LIST_URL = "https://api.steampowered.com/ISteamApps/GetAppList/v2/"
CACHE_FILE = Path(".steam_apps_cache.json")
CACHE_TTL_SECONDS = 60 * 60 * 24  # 24h


def format_app_display(app_id: int, name: str | None) -> str:
    """Return `Name (ID)` when the name is available, otherwise just the ID."""
    app_id_str = str(app_id)
    raw_name = (name or "").strip()
    if not raw_name:
        return app_id_str

    suffix = f" ({app_id_str})"
    if raw_name.endswith(suffix):
        trimmed = raw_name[: -len(suffix)].rstrip(" -")
        raw_name = trimmed or raw_name

    return f"{raw_name} ({app_id_str})"


class SteamAppDirectory:
    def __init__(self, cache_path: Path | str | None = None, cache_ttl: int = CACHE_TTL_SECONDS) -> None:
        self.cache_path = Path(cache_path) if cache_path else CACHE_FILE
        self.cache_ttl = cache_ttl
        self._names: Dict[int, str] = {}
        self._loaded = False

    def get_name(self, app_id: int) -> str:
        self._ensure_loaded()
        name = self._names.get(int(app_id))
        return (str(name).strip()) if name else ""

    def format_entry(self, app_id: int) -> str:
        return format_app_display(app_id, self.get_name(app_id))

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if not self._load_cache():
            self._fetch_and_cache()
        self._loaded = True

    def _load_cache(self) -> bool:
        if not self.cache_path.exists():
            return False
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        updated_at = payload.get("updated_at") or 0
        if time.time() - updated_at > self.cache_ttl:
            return False
        apps = payload.get("apps") or {}
        self._names = {int(app_id): str(name) for app_id, name in apps.items()}
        return True

    def _fetch_and_cache(self) -> None:
        try:
            response = requests.get(APP_LIST_URL, timeout=30)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException:
            return

        apps_raw = (data.get("applist") or {}).get("apps") or []
        names: Dict[int, str] = {}
        for entry in apps_raw:
            try:
                app_id = int(entry.get("appid"))
            except (TypeError, ValueError):
                continue
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            names[app_id] = name
        if names:
            self._names = names
            self._save_cache()

    def remember_name(self, app_id: int, name: str | None) -> None:
        normalized = (name or "").strip()
        if not normalized:
            return
        self._names[int(app_id)] = normalized
        self._save_cache()

    def _save_cache(self) -> None:
        if not self._names:
            return
        payload = {
            "updated_at": int(time.time()),
            "apps": {str(app_id): label for app_id, label in self._names.items()},
        }
        try:
            self.cache_path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass

