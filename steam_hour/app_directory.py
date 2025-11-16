from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict

import requests

APP_LIST_URL = "https://api.steampowered.com/ISteamApps/GetAppList/v2/"
CACHE_FILE = Path(".steam_apps_cache.json")
CACHE_TTL_SECONDS = 60 * 60 * 24  # 24h


class SteamAppDirectory:
    def __init__(self, cache_path: Path | str | None = None, cache_ttl: int = CACHE_TTL_SECONDS) -> None:
        self.cache_path = Path(cache_path) if cache_path else CACHE_FILE
        self.cache_ttl = cache_ttl
        self._names: Dict[int, str] = {}
        self._loaded = False

    def get_name(self, app_id: int) -> str:
        self._ensure_loaded()
        name = self._names.get(int(app_id))
        return name or f"App {app_id}"

    def format_entry(self, app_id: int) -> str:
        name = self.get_name(app_id)
        if name.startswith("App "):
            return str(app_id)
        return f"{app_id} ({name})"

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
            payload = {"updated_at": int(time.time()), "apps": names}
            try:
                self.cache_path.write_text(json.dumps(payload), encoding="utf-8")
            except OSError:
                pass

